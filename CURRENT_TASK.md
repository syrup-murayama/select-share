# CURRENT_TASK — select-share-runner.lrplugin

最終更新: 2026-04-28

---

## 完了した作業

- `select-share-runner.lrplugin` を新規作成
  - `Info.lua` — プラグインマニフェスト（LrSDK 6.0）
  - `RunDialog.lua` — ダイアログ UI + build.py 実行ロジック
  - `ImportMenuItem.lua` — adopted_list.txt 読み込み → カタログ照合 → ピック/コレクション作成
  - `select-share/build.py` — ライブラリを同梱（配布対応）
  - `README.md` — インストール手順・使い方（日本語）
  - `LICENSE` — MIT License
- exiftool ダイアログを改善（「ダウンロードページを開く」ボタン付き）
- 動作確認済み（ビルダー・採用リスト読み込みともに）

---

## 設計上の重要な判断

### 配布可能設計
- build.py はプラグイン内に同梱（`select-share/build.py`）
- パス解決は `_PLUGIN.path` ベース（ハードコードなし）
- 将来的に `LrHttp` でリモートから build.py を更新取得できる構造

### exiftool 対応
- Apple Silicon (`/opt/homebrew/bin`) と Intel (`/usr/local/bin`) 両方を検索
- 見つからない場合は公式サイト（https://exiftool.org/）を開くボタン付きダイアログを表示
- `PATH=<exiftool_dir>:"$PATH"` をコマンドに付与してシェル環境の差異を吸収

### UI 設計
- フォルダ選択は「参照ボタン + `static_text`」パターン（`edit_field` は外部書き込みが UI に反映されない）
- `props` は `LrBinding.makePropertyTable(context)` で生成（observable テーブル）
- LrC SDK モジュールは `import` で読み込む（`require` 不可）

### 採用リスト照合
- カタログ全写真の stem を取得し、adopted stem のサブストリングとして照合
- ステム先頭の日付（`YYYYMMDD`）でカタログ検索を絞り込み
- 非インデントのフリーテキストは「見つからず」リストに出るが実害なし → 放置

### 配布戦略
- 無料・オープンソース（MIT）で公開
- ツールはブランド構築・コミュニティ貢献が目的。収益は別途サービスで立てる

---

## 次にやるべきステップ

1. **GitHub Releases 設定** — ZIP パッケージを作成してリリースを切る
2. **フォーマットバージョニング** — `adopted_list.txt` 先頭に `# format: v1` を埋め込み、将来の後方互換に備える
3. **将来: exiftool バンドル対応**（Gatekeeper 問題があるため現状は保留）
4. **将来: build.py 自動更新機能**（`LrHttp` でリモートから最新 build.py を取得）

---

## ファイル構成

```
select-share-runner.lrplugin/
├── Info.lua
├── RunDialog.lua
├── ImportMenuItem.lua
├── README.md
├── LICENSE
└── select-share/
    └── build.py
```
