# CURRENT_TASK — select-share-runner.lrplugin / select-share

最終更新: 2026-05-28

---

## 完了した作業

### select-share-runner.lrplugin（v1.0.2）
- RunDialog.lua / ImportMenuItem.lua / README / LICENSE 整備・動作確認済み
- 全バインディングバグ修正（bind_to_object=props 明示）
- no_copy/move を「ファイル処理」popup に統合
- exiftool 未インストール時: ダウンロードページを開くダイアログ
- git subtree で github.com/syrup-murayama/select-share に配信体制構築

### select-share / build.py
- ダウンロードファイル名にサイトタイトルを反映
  - JSON保存: `{title}-saved-YYYY-MM-DD.json`
  - 採用リスト: `{title}-adopted_list.txt`
- UI ヘッダー再設計（左→右フロー）
  - 「4枚採用」ピル廃止 → 「採用リストを出力（N枚）」ボタンに統合
  - タグ集計をヘッダーボタン直下ドロップダウンに変更（デフォルト非表示）
  - 操作フロー: 保存/読み込み → 表示切替 → タグ集計 → 採用リストを出力

---

## 設計上の重要な判断

### バインディング（Lr プラグイン）
- `LrView.bind { key='...', bind_to_object=props }` を全コントロールに明示
- フォルダ表示は `static_text` + `truncation='middle'`

### ファイル処理
- `--no-copy` は UI から除外（参照のみではリンク切れリスク）
- 「コピー」「移動」の2択を `file_handling` popup で管理

### リポジトリ
- 開発: `photo-workflow/` モノレポ（main 一本）
- 配信: `git subtree push --prefix=lr-plugin/select-share-runner.lrplugin select-share main`
- コマンドメモ: `photo-workflow/.git/SELECT_SHARE_SUBTREE.md`

---

## 次にやるべきステップ

1. **GitHub Releases 設定** — ZIP パッケージを作成してリリースを切る
2. **フォーマットバージョニング** — `adopted_list.txt` 先頭に `# format: v1` を埋め込み
3. **将来: build.py 自動更新**（`LrHttp` でリモートから取得）
