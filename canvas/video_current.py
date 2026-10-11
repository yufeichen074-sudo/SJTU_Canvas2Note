"""Adapter for SJTU's current Canvas LTI recording service (October 2026)."""
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import requests
import shutil
from bs4 import BeautifulSoup
from .video_client import SJTUVideoClient, CanvasVideo, VideoInfo, VideoPlayInfo, VideoDownloadProgress


class CurrentVideoClient(SJTUVideoClient):
    api_base = 'https://v.sjtu.edu.cn/jy-application-resourcemanage'

    def login(self, ja_auth_cookie='', canvas_token=''):
        self._ja_auth_cookie = ja_auth_cookie or self._ja_auth_cookie
        return self.login_canvas_website()

    def bind_canvas_course(self, course_id):
        if self._canvas_course_id == str(course_id):
            return
        if not self.login_canvas_website():
            raise RuntimeError('Canvas login expired')
        r = self.session.get(f'https://oc.sjtu.edu.cn/courses/{course_id}/external_tools/8329', timeout=30)
        for step in range(2):
            r.raise_for_status()
            form = BeautifulSoup(r.text, 'html.parser').find('form')
            if form is None:
                raise RuntimeError('Canvas recording launch form unavailable')
            action = urljoin(r.url, form.get('action', ''))
            if urlparse(action).hostname != 'v.sjtu.edu.cn':
                raise RuntimeError('Unexpected recording authentication destination')
            data = {i['name']: i.get('value', '') for i in form.find_all('input') if i.get('name')}
            r = self.session.post(action, data=data, timeout=30, allow_redirects=(step == 0))
        p = urlparse(r.headers.get('Location', ''))
        token = parse_qs(p.fragment.split('?')[-1]).get('jwt_token', [None])[0]
        if not token:
            raise RuntimeError('Recording launch token unavailable')
        self._new_headers = {'jwt-token': token}
        ctx = self._api_get('/lms/launch-context')
        self._tecl_id = ctx['canvasRecord']['teachingClassId']
        self._canvas_course_id = str(course_id)

    def _api_get(self, path, params=None):
        r = self.session.get(self.api_base + path, params=params, headers=self._new_headers, timeout=30)
        r.raise_for_status()
        body = r.json()
        if body.get('status') != 200:
            raise RuntimeError('Recording service rejected request')
        return body.get('data')

    def list_videos(self, course_id):
        self.bind_canvas_course(course_id)
        data = self._api_get('/v1/subject_vod_list_new', {'page.pageIndex': 1, 'page.pageSize': 1000, 'teclIds': self._tecl_id, 'page.orders[0].asc': 'false', 'page.orders[0].field': 'courBeginTime'})
        videos = []
        for r in data.get('records', []):
            begin = datetime.fromisoformat(r['courBeginTime'])
            end = datetime.fromisoformat(r['courEndTime'])
            title = f"{r['courBeginTime']} 第{r.get('letiNumber', '')}节"
            videos.append(CanvasVideo(id=str(r['id']), title=title, duration=int((end-begin).total_seconds()), cour_id=str(r['id'])))
        return videos

    def get_video_info(self, video_id, video_title=''):
        data = self._api_get('/v1/course_vod_urls_new', {'courseId': video_id})
        plays = [VideoPlayInfo(id=str(video_id), name=f"轨道 {p['viewNum']}", index=int(p['viewNum']), rtmp_url_hdv=p['url']) for p in data.get('courseVodViewList', []) if p.get('url')]
        duration = max([p.get('vodTime', 0) for p in data.get('courseVodViewList', [])] or [0])
        return VideoInfo(id=str(video_id), title=video_title or data.get('courName', ''), duration=duration, plays=plays, rtmp_url_hdv=plays[0].rtmp_url_hdv if plays else '')

    def get_ppt_slides(self, cour_id):
        raise RuntimeError('新版平台的 PPT 导出尚未接入，请使用课堂板书截图。')

    def download_video(self, video_id, save_path, title='', play_index=-1, progress_handler=None):
        save_path = Path(save_path)
        if save_path.exists():
            return save_path
        info = self.get_video_info(video_id, title)
        if not info.plays:
            raise RuntimeError('No recording streams available')
        play = next((p for p in info.plays if p.index == play_index), info.plays[0])
        save_path.parent.mkdir(parents=True, exist_ok=True)
        part = save_path.with_suffix(save_path.suffix + '.part')
        # Completed range files can be resumed; sparse partial MP4s are never reused.
        with requests.get(play.rtmp_url_hdv, stream=True, timeout=30, headers={'Range': 'bytes=0-0', 'Referer': 'https://v.sjtu.edu.cn/'}) as probe:
            probe.raise_for_status()
            cr = probe.headers.get('Content-Range', '')
            total = int(cr.split('/')[-1]) if probe.status_code == 206 and '/' in cr else 0
        if total:
            parts_dir = save_path.parent / (save_path.name + f'.{video_id}.{play.index}.parts')
            parts_dir.mkdir(exist_ok=True)
            step = max((total+7)//8, 1)
            ranges = [(i, min(i+step-1, total-1)) for i in range(0, total, step)]
            counts = [0]*len(ranges)
            lock = Lock()
            def fetch_range(item):
                number, bounds = item
                first, last = bounds
                chunk_path = parts_dir / f'{first}-{last}.chunk'
                if chunk_path.exists() and chunk_path.stat().st_size == last-first+1:
                    with lock:
                        counts[number] = last-first+1
                        if progress_handler:
                            progress_handler(VideoDownloadProgress(str(video_id), sum(counts), total))
                    return chunk_path
                tmp = chunk_path.with_suffix('.tmp')
                with requests.get(play.rtmp_url_hdv, stream=True, timeout=60, headers={'Range': f'bytes={first}-{last}', 'Referer': 'https://v.sjtu.edu.cn/'}) as r:
                    r.raise_for_status()
                    if r.status_code != 206 or not r.headers.get('Content-Range', '').startswith(f'bytes {first}-{last}/'):
                        raise RuntimeError('Media server did not honor requested range')
                    written = 0
                    with tmp.open('wb') as f:
                        for chunk in r.iter_content(1024*1024):
                            f.write(chunk)
                            written += len(chunk)
                            with lock:
                                counts[number] = written
                                if progress_handler:
                                    progress_handler(VideoDownloadProgress(str(video_id), sum(counts), total))
                    if written != last-first+1:
                        raise RuntimeError('Incomplete recording range')
                tmp.replace(chunk_path)
                return chunk_path
            with ThreadPoolExecutor(max_workers=8) as ex:
                chunks = list(ex.map(fetch_range, enumerate(ranges)))
            with part.open('wb') as dest:
                for chunk_path in chunks:
                    with chunk_path.open('rb') as src:
                        shutil.copyfileobj(src, dest, 1024*1024)
            if part.stat().st_size != total:
                raise RuntimeError('Incomplete recording download')
            part.replace(save_path)
            for chunk_path in chunks:
                chunk_path.unlink()
            if not any(parts_dir.iterdir()):
                parts_dir.rmdir()
            return save_path
        # No JWT is sent to the media host; the signed URL authorizes this stream.
        with self.session.get(play.rtmp_url_hdv, stream=True, timeout=60, headers={'Referer': 'https://v.sjtu.edu.cn/'}) as r:
            r.raise_for_status()
            total = int(r.headers.get('Content-Length', 0))
            count = 0
            with part.open('wb') as f:
                for chunk in r.iter_content(1024*1024):
                    f.write(chunk)
                    count += len(chunk)
                    if progress_handler:
                        progress_handler(VideoDownloadProgress(str(video_id), count, total))
            if total and count != total:
                raise RuntimeError('Incomplete recording download')
        part.replace(save_path)
        return save_path
