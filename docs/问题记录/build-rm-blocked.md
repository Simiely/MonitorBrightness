# 问题：`rm -rf dist && pyinstaller` 打包没执行

**TL;DR**：shell 把 `rm` 包装成"移入回收站"，删除被拦截返回非 0，导致 `&&` 链短路。

- **问题**：删旧目录的命令失败后，后面的打包命令根本没跑，却看不出报错。
- **根因**：环境对 `rm` 做了 safe-delete（回收站）包装，删除动作被安全策略拦截。
- **解决**：不要删旧目录，改用**全新输出目录**：
  `--distpath dist_new --workpath build_new --specpath build_new`。
- **预防**：构建脚本避免"先删后建"，一律输出到新目录。
