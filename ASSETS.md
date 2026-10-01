# 素材来源与运行方式

本项目素材来自用户提供的 whale-pet 动画包。本次打包保留原始像素，不生成新的角色画面。

- `app/assets/atlas.json` 与三页 `atlas*.png`：原有 1224 帧，包含镜像别名。
- `app/assets/extra/ds_code_eureka`：`whale-pet-frames-code-eureka.zip` 中的无主气泡版本，76 帧。
- `app/assets/extra/ds_tail_allow`：`whale-pet-frames-tail-allow.zip` 中的无文字版本，60 帧；`source.json` 记录原始 ZIP 和各 PNG 的 SHA-256。
- `app/assets/extra/additions.json`：吃饱揉肚子及游泳，共 257 张原始透明 PNG（小尺寸帧仅存档）；完整尺寸的左右向由 swim-full 两个素材卷合并，保留 10 fps、转向、点击跃出和逐帧 SHA-256。
- 对话气泡由程序实时绘制，可关闭。摸尾巴的确认和超时片段独立，不会串播。
- `docs/previews/tail-allow.png`：应用实际渲染的分支预览。

原始素材 ZIP、生成原画和本机运行环境没有重复放入仓库。
