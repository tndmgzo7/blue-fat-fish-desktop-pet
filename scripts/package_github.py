"""Build a clean, reproducible GitHub source package with a strict file allowlist."""
from pathlib import Path
import argparse,hashlib,json,re,shutil,stat,zipfile

BASE=Path(__file__).resolve().parents[1]
REPOSITORY=Path(__file__).resolve().parent.name=='scripts' and (BASE/'app/whale_pet.py').is_file()
ROOT=BASE if REPOSITORY else Path(__file__).resolve().parents[3]
APP=ROOT/'app' if REPOSITORY else ROOT/'桌宠/whale-pet/app'
TOOLS=ROOT/'scripts' if REPOSITORY else ROOT/'桌宠/whale-pet/tools'
VERSION='2026.09.30'
NAME='blue-fat-fish'
# Leak check: reject any local absolute path instead of listing specific machines' paths.
PERSONAL_PATH_PATTERNS=(
    re.compile(rb'(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]+(?:Users|Documents and Settings)[\\/]'),  # Windows user home
    re.compile(rb'(?<![A-Za-z0-9])[A-Za-z]:\\[A-Za-z0-9_.$~-]'),                             # Windows drive-letter absolute path
)
LEAK_CHECK_SUFFIXES=('.py','.md','.mjs','.json','.bat','.txt','.command')
README=r'''# 蓝色大肥鱼桌宠 / Blue Fat Fish Desktop Pet

一只透明、无边框的蓝色鲸鱼伙伴。可以独立陪伴，也可以跟随本机 DeepSeek Harness（dsh）的工作状态。

![摸尾巴允许动画](docs/previews/tail-allow.png)

## 实机截图

<p>
  <img src="docs/screenshots/desktop-1.png" alt="实机截图：开心冒爱心" width="49%">
  <img src="docs/screenshots/desktop-2.png" alt="实机截图：扑蝴蝶" width="49%">
</p>

## 开始使用

Windows 安装 Python 3.11 或更新版本，并让 Python 可从命令行使用。解压完整目录后，双击 **启动桌宠.bat**；首次启动会自动创建环境并安装 PySide6，需要网络。

- 单击摸头、双击开心；按住拖动，松开后落到屏幕底部。
- 点尾巴或右键「互动 → 摸尾巴」：先甩尾巴，再转身允许摸一下。等待约 4 秒，点她进入被摸分支；没有点击则进入超时分支。两个分支独立播放。
- 右键可以投喂、玩耍、改大小、切换安静陪伴、隐藏和退出。重复启动会召回已有实例。
- **打开桌宠对话.bat** 打开单行半透明输入框：回车发送，Alt + 拖动移动，右键设置，悬停查看反馈。
- 独立模式指令：喂饭、睡觉、醒醒、安静、活泼、隐藏、散步、写代码、摸尾巴、摸头、开心。

macOS 可运行 `bash app/run_mac.command`；源码和素材通用，本次桌面与 dsh 联调在 Windows 验证。

## 连接 dsh

先安装并运行一次 DeepSeek Harness 桌面版，双击 **接入本地DSH.bat**，再双击 **启动DSH桌宠.bat**。安装脚本从当前用户的 `.dsh` 或 `DSH_HOME` 找配置，备份后添加可移除的插件段。

输入框右键选择 / 新建会话、设置工作目录、排队或插入当前工作、停止工作。许可和工具问题在 dsh 窗口中处理。运行时才会生成个人设置和本机连接凭据，这些文件不在源码包内。

动画跟随读取、搜索、修改、执行、思考、回复、委派、等待、限流、完成和失败。待机动作有冷却、防重复和轮换补偿。详情见 [状态与频率](docs/动画状态说明.md) 和 [dsh 使用说明](docs/DSH使用说明.md)。

## 开发与验证

应用使用 Python 标准库和 PySide6；dsh 桥接使用 Node.js 内置模块，由 dsh 加载，无须额外 npm 安装。

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r app\requirements.txt
.venv\Scripts\python.exe app\whale_pet.py --mode standalone
.venv\Scripts\python.exe -m unittest discover -s app\tests -p "test_*.py"
.venv\Scripts\python.exe app\whale_pet.py --selftest --out selftest_out
```

桥接测试（需要 Node.js）：`node --test app/tests/bridge.test.mjs`。

重新生成源码包：`python scripts/package_github.py`，输出到 `release/`。打包采用文件白名单，校验每个文件并附上 `MANIFEST.json`。

## 上传 GitHub

解压本包，把 `blue-fat-fish` 里面的文件作为仓库根目录上传，保留 `app/assets` 的全部文件。不要把外层 ZIP 当作源码目录；ZIP 可以放在 GitHub Release 供下载。

仓库包含启动器、源码、完整三页图集、76 帧写代码动画、60 帧摸尾巴允许动画、测试与说明；不含 Python 环境、会话、个人设置、连接凭据、历史原始 ZIP 或临时验证文件。素材来源记录见 [ASSETS.md](ASSETS.md)。

## 授权 / Credits

- **代码**（`app/` 里的 `.py` / `.mjs` / 脚本、`scripts/`、启动器、配置）：MIT License，见 [`LICENSE`](LICENSE)。
- **美术素材**（`app/assets/` 里的图集与图标、`app/assets/extra/` 里的序列帧、`docs/previews/` 里的预览图）：[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans)，见 [`LICENSE-ART`](LICENSE-ART)。**禁止商用**。
- 本项目是 DeepSeek 鲸鱼娘 /「蓝色大肥鱼」形象的**二创（fan work）**：
  - 原设计：B站「上善无形」
  - 女仆版：B站 ZipZipPipe
  - 本项目三视图为基于 ZipZipPipe 女仆版的 AI 生成二创参考，所有动画帧由此制作（参考图不随仓库分发）。
- 角色形象的权利归原作者所有；上述授权只涵盖本项目自己制作的部分（动画、帧、程序）。如原作者有任何异议，请开 issue 联系，会及时处理。
- 与 DeepSeek（深度求索）官方**无任何关联**，也未获其认可或赞助；“DeepSeek”名称仅用于说明二创来源。
'''
DSH_GUIDE='''# 蓝色大肥鱼与 dsh

安装并启动一次 DeepSeek Harness 桌面版后，运行根目录「接入本地DSH.bat」。默认读取 `%USERPROFILE%/.dsh`，自定义目录用 `DSH_HOME`。已验证 Desktop 0.2.0-rc.1 的 SessionController 接口。

脚本向 `profiles/desktop/cordis.patch.yml` 添加 `whale-pet-bridge` 标记区域，保留其余配置并生成备份。移动本项目后重新运行接入脚本以更新插件地址。退出接入时只删除 begin / end 标记区域。CLI / Web 不会被此脚本自动修改。

双击「启动DSH桌宠.bat」。单行输入框按回车发送，Alt + 拖动移动，右键选择 / 新建会话、设置工作目录、切换独立模式、停止工作。默认跟随正在工作的会话；手动选会话会关闭自动跟随，右键可以重新开启。

输入默认排队，启用「插入当前工作」后提交到当前回合。发送失败会保留输入，不自动重复发送。需要许可或回答工具问题时在 dsh 窗口操作。桥接只监听本机并使用运行时随机凭据；不要上传 `app/userdata`。

模型配置由 dsh 管理，源码包不包含任何模型 API 密钥。完整工作状态映射见 [动画状态说明](动画状态说明.md)。
'''
GITIGNORE='''# Local environments and generated outputs
.venv/
.venv.*/
**/.venv/
**/.venv.*/
__pycache__/
*.py[cod]
userdata/
**/userdata/
_validation/
**/_validation/
selftest_out/
**/selftest_out/
build/
dist/
release/
*.zip
*.log
.env
.env.*
*.bak
.DS_Store
Thumbs.db
'''
ATTRIBUTES='''* text=auto
*.py text eol=lf
*.mjs text eol=lf
*.json text eol=lf
*.md text eol=lf
*.txt text eol=lf
*.bat text eol=crlf
*.command text eol=lf
*.sh text eol=lf
*.png binary
*.gif binary
*.ico binary
*.icns binary
'''
CHANGELOG='''# 更新记录

## 2026.09.30

- 角色名称统一为蓝色大肥鱼，单行半透明无边框指令框。
- 新增摸尾巴允许动画：60 帧，等待确认、被摸、超时三个片段；连接原有甩尾巴动作，菜单和独立指令均可触发。
- 新增 76 帧写代码 → 灵光一闪，接入本机 dsh 的真实工作状态。
- 动画冷却、防重复和长期未播补偿，保护手动互动，降低重复散步和讨饭频率。
- 帧缓存、设置保存、单实例、屏幕边界、隐藏 / 召回、安静陪伴和托盘菜单。
- 提供完整素材的 GitHub 源码包及文件校验清单。
'''
ASSETS='''# 素材来源与运行方式

本项目素材来自用户提供的 whale-pet 动画包。本次打包保留原始像素，不生成新的角色画面。

- `app/assets/atlas.json` 与三页 `atlas*.png`：原有 1224 帧，包含镜像别名。
- `app/assets/extra/ds_code_eureka`：`whale-pet-frames-code-eureka.zip` 中的无主气泡版本，76 帧。
- `app/assets/extra/ds_tail_allow`：`whale-pet-frames-tail-allow.zip` 中的无文字版本，60 帧；`source.json` 记录原始 ZIP 和各 PNG 的 SHA-256。
- 对话气泡由程序实时绘制，可关闭。摸尾巴的确认和超时片段独立，不会串播。
- `docs/previews/tail-allow.png`：应用实际渲染的分支预览。

原始素材 ZIP、生成原画和本机运行环境没有重复放入仓库。
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/('release' if REPOSITORY else '发布'))
    args=parser.parse_args(); output=args.out.resolve(); output.mkdir(parents=True,exist_ok=True)
    stage=output/NAME; stage.mkdir(exist_ok=True)
    entries={}
    def add(destination,data):
        rel=Path(destination)
        if rel.is_absolute() or '..' in rel.parts: raise ValueError('Unsafe package path')
        entries[rel.as_posix()]=data
    def copy(source,destination):
        if source.is_symlink() or not source.is_file(): raise ValueError('Missing regular source file: '+str(source))
        data=source.read_bytes()
        if source.suffix in ('.py','.mjs','.md','.json','.txt','.command','.bat'):
            text=data.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
            data=(text.replace('\n','\r\n') if source.suffix=='.bat' else text).encode('utf-8')
        add(destination,data)
    for name in ('whale_pet.py','pet_support.py','dsh_companion.py','animation_policy.py','requirements.txt','run_windows.bat','run_mac.command'):
        copy(APP/name,'app/'+name)
    for path in sorted((APP/'assets').rglob('*')):
        if path.is_file() and path.suffix in ('.png','.json','.ico','.icns'):
            copy(path,'app/assets/'+path.relative_to(APP/'assets').as_posix())
    copy(APP/'dsh_plugin/index.mjs','app/dsh_plugin/index.mjs')
    copy(TOOLS/'install_dsh_bridge.py','scripts/install_dsh_bridge.py')
    copy(Path(__file__),'scripts/package_github.py')
    test_folder=APP/'tests'
    for name in ('test_tail_allow.py','test_animation_policy.py','bridge.test.mjs'):
        candidate=test_folder/name
        if not candidate.exists(): candidate=APP/'_validation'/name
        copy(candidate,'app/tests/'+name)
    preview=ROOT/'docs/previews/tail-allow.png' if REPOSITORY else APP/'_validation/preview_tail_allow.png'
    copy(preview,'docs/previews/tail-allow.png')
    for name in ('desktop-1.png','desktop-2.png'):
        copy(ROOT/'docs/screenshots'/name,'docs/screenshots/'+name)
    policy_path=ROOT/'docs/动画状态说明.md' if REPOSITORY else ROOT/'动画状态说明.md'
    policy=policy_path.read_text(encoding='utf-8').replace('桌宠/whale-pet/app/','app/')
    add('docs/动画状态说明.md',policy.encode('utf-8'))
    for name in ('LICENSE','LICENSE-ART'):
        copy(ROOT/name,name)
    if REPOSITORY: copy(ROOT/'README.md','README.md')
    else: add('README.md',README.encode('utf-8'))
    for name,text in {'docs/DSH使用说明.md':DSH_GUIDE,'.gitignore':GITIGNORE,'.gitattributes':ATTRIBUTES,
                      'CHANGELOG.md':CHANGELOG,'ASSETS.md':ASSETS,'VERSION':VERSION+'\n'}.items(): add(name,text.encode('utf-8'))
    launchers={'启动桌宠.bat':'--mode standalone','启动DSH桌宠.bat':'--mode dsh --chat','打开桌宠对话.bat':'--chat'}
    for name,arguments in launchers.items():
        text='@echo off\r\nchcp 65001 >nul\r\ncall "%~dp0app\\run_windows.bat" '+arguments+' %*\r\n'
        add(name,text.encode('utf-8'))
    bridge_launcher=(ROOT/'接入本地DSH.bat').read_text(encoding='utf-8-sig')
    bridge_launcher=bridge_launcher.replace('桌宠\\whale-pet\\app\\','app\\').replace('桌宠\\whale-pet\\tools\\','scripts\\')
    add('接入本地DSH.bat',bridge_launcher.replace('\r\n','\n').replace('\n','\r\n').encode('utf-8'))
    expected=set(entries)|{'MANIFEST.json'}
    unexpected=[path.relative_to(stage).as_posix() for path in stage.rglob('*') if path.is_file() and path.relative_to(stage).as_posix() not in expected]
    if unexpected: raise ValueError('Unexpected files in staging directory; keep them out of the package: '+str(unexpected))
    manifest=[]
    for name,data in sorted(entries.items()):
        if len(data)>20_000_000: raise ValueError('Split oversized source asset before publishing: '+name)
        if any(part in ('.venv','userdata','_validation','__pycache__','.git') for part in Path(name).parts): raise ValueError('Private/generated path in source package')
        if (name.endswith(LEAK_CHECK_SUFFIXES) or name in ('LICENSE','LICENSE-ART','VERSION')) and any(p.search(data) for p in PERSONAL_PATH_PATTERNS):
            raise ValueError('Personal absolute path in package: '+name)
        path=stage/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        manifest.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    record={'name':NAME,'version':VERSION,'kind':'source','files':manifest}
    data=(json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    (stage/'MANIFEST.json').write_bytes(data); entries['MANIFEST.json']=data
    archive=output/('蓝色大肥鱼-GitHub源码包-'+VERSION+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(entries.items()):
            info=zipfile.ZipInfo(NAME+'/'+name,date_time=(2026,9,30,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED; info.create_system=3
            info.external_attr=(stat.S_IFREG|(0o755 if name.endswith('.command') else 0o644))<<16
            z.writestr(info,data,compresslevel=9)
    with zipfile.ZipFile(archive) as z:
        if z.testzip(): raise ValueError('ZIP CRC verification failed')
        for file in manifest:
            if hashlib.sha256(z.read(NAME+'/'+file['path'])).hexdigest()!=file['sha256']: raise ValueError('Package hash mismatch')
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/(archive.name+'.sha256')).write_text(checksum+'  '+archive.name+'\n',encoding='utf-8')
    print(json.dumps({'folder':str(stage),'archive':str(archive),'files':len(entries),'bytes':archive.stat().st_size,
                      'largest_file_bytes':max(row['bytes'] for row in manifest),'sha256':checksum},ensure_ascii=True))

if __name__=='__main__': main()