# 通用工具流程

安装已应用本包补丁的 Canvas2Note，按上游说明准备 Python 环境与前端。技能脚本使用该环境的 Python，不重复安装工具或模型。

设置环境变量 `SJTU_CANVAS2NOTE_ROOT` 指向自己的工具仓库；或将 `local_config.example.json` 复制为同目录 `local_config.json`，填入自己的绝对工具路径。实际配置应保留在本机，不提交Git。

优先顺序：当前录像字幕与课件 → 无字幕再下载一条合适轨道 → 本地ASR → 根据转写选板书时间点 → 核对公式、题面与证明 → 四部分课堂笔记。不要复用其他课的课程ID、录像ID或截图时间。

通过当前提供的浏览器工具操作 Edge。需要登录时让用户正常扫码；不要输出或共享 Cookie、JWT、密码及带签名媒体链接。

## 助手命令

使用工具环境的 Python 执行技能 `scripts/course_media.py --help`。
- `doctor`：只检查环境，不登录、下载或运行ASR。
- `videos --course-id ID`：列当前课程录播。
- `download --course-id ID --video-id ID --output ABSOLUTE_MP4_PATH --track NUMBER`：下载一条录像。
- `transcribe --video ABSOLUTE_MP4_PATH --output-dir DIRECTORY --stem NAME --model medium --device auto`：生成TXT/SRT/JSON。自动模式优先符合条件的GPU，否则CPU；内存不足或GPU异常可重新以CPU运行，未完成的.part不是完整结果。
- `frames --video ABSOLUTE_MP4_PATH --output-dir DIRECTORY --times MM:SS MM:SS`：原始录像提帧，保存真实帧时间、分辨率和索引。

`scripts/start_tool.ps1` 是Windows启动入口，要求仓库使用 `.venv/Scripts/python.exe`，仅监听本机并打开Edge。其他系统按上游方式启动服务。该脚本不修改执行策略、不安装系统服务。

## 验证与保存

ASR草稿中的数学符号、字母和数字仍需板书/课件核对。课堂例题、证明与额外补充明确分开；没有原题不补造。真实截图不得生成、补画或改写板书。

材料保存在当次工作目录；本地文件不等于上传到云端项目来源。遵循用户指定的输出位置，项目只读参考文件不可改动。

接口路径与边界见包根目录README和TEST_REPORT。新版PPT导出尚未实现，整课及其他课程尚待真实使用验证。
