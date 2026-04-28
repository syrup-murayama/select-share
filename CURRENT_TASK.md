# CURRENT_TASK — select-share-runner.lrplugin

最終更新: 2026-04-24

---

## 完了した作業

- `select-share-runner.lrplugin` を新規作成
  - `Info.lua` — プラグインマニフェスト（LrSDK 6.0）
  - `RunDialog.lua` — ダイアログ UI + build.py 実行ロジック
  - `select-share/build.py` — ライブラリを同梱（配布対応）
- 動作確認済み（フォルダ選択 → ビルド実行）

---

## 設計上の重要な判断

### 配布可能設計
- build.py はプラグイン内に同梱（`select-share/build.py`）
- パス解決は `_PLUGIN.path` ベース（ハードコードなし）
- 将来的に `LrHttp` でリモートから build.py を更新取得できる構造

### exiftool 対応
- Apple Silicon (`/opt/homebrew/bin`) と Intel (`/usr/local/bin`) 両方を検索
- 見つからない場合は `brew install exiftool` 手順を示すダイアログを表示
- `PATH=<exiftool_dir>:"$PATH"` をコマンドに付与してシェル環境の差異を吸収

### UI 設計
- フォルダ選択は `edit_field` ではなく「参照ボタン + `static_text`」パターン
  - 理由: LrC SDK では外部からの `edit_field` 書き込みが UI に反映されない
- 参照ボタンの `action` から `LrDialogs.runOpenPanel` を直接呼び出す（`LrTasks.startAsyncTask` でラップしない）
- `props` は `LrBinding.makePropertyTable(context)` で生成（observable テーブル）
- LrC SDK モジュールは `import` で読み込む（`require` 不可）

---

## 次にやるべきステップ

1. **動作テスト継続** — 各オプション（--move, --no-zip, --theme 等）の動作確認
2. **バージョン管理方針の決定** — `Info.lua` の VERSION を手動管理、git タグで運用
3. **将来: exiftool バンドル対応**（フル自己完結配布が必要になった場合）
   - `bin/exiftool` をプラグイン内に同梱
   - `findExiftool()` でプラグイン内バイナリを優先検索するよう拡張
4. **将来: build.py 自動更新機能**（OSS 配布後）
   - `LrHttp` でリモートから最新 build.py を取得する仕組み

---

## ファイル構成

```
/Users/daisuke/src/photo-workflow/lr-plugin/select-share-runner.lrplugin/
├── Info.lua
├── RunDialog.lua
└── select-share/
    └── build.py   ← /Users/daisuke/src/photo-workflow/select-share/build.py のコピー
```
