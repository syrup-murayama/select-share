# Select Share Runner

Lightroom Classic プラグイン。[select-share](https://github.com/syrup-murayama/photo-workflow) で生成した納品ギャラリーのビルドと、クライアントからの採用リスト読み込みを Lightroom の中から行えます。

---

## 機能

### Select Share ビルダー
JPEG フォルダを指定して `build.py` を実行し、納品用 HTML ギャラリーを生成します。

### セレクト結果を読み込む
クライアントから受け取った `adopted_list.txt` を読み込み、対応する写真にピックフラグ・レーティングを付け、コレクションを自動作成します。

---

## 必要なもの

- **Lightroom Classic** 6.0 以上
- **macOS**
- **exiftool** — 下記手順でインストール

### exiftool のインストール

1. [https://exiftool.org/](https://exiftool.org/) を開く
2. **macOS Package** をダウンロード（`ExifTool-XX.XX.pkg`）
3. ダブルクリックしてインストール

> ターミナルの操作は不要です。

---

## インストール

1. [Releases](https://github.com/syrup-murayama/photo-workflow/releases) から最新の `select-share-runner.lrplugin.zip` をダウンロード
2. ZIP を展開する
3. Lightroom Classic を開き、**プラグインマネージャー**（メニュー: ファイル → プラグインマネージャー）を開く
4. 「追加」ボタンをクリックし、展開した `select-share-runner.lrplugin` フォルダを選択
5. プラグインが「インストール済みで実行中」になれば完了

---

## 使い方

### ビルダー

1. ライブラリメニュー → **Select Share ビルダー...**
2. 入力フォルダ（JPEG が入ったフォルダ）と書き出し先を選択
3. 必要に応じてオプションを設定し、「実行」をクリック

### 採用リスト読み込み

1. ライブラリメニュー → **セレクト結果を読み込む…**
2. クライアントから受け取った `adopted_list.txt` を選択
3. 内容を確認して「適用する」をクリック
4. 対象写真にピックフラグとレーティングが付き、コレクション「採用 YYYY-MM-DD」が作成される

---

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照してください。

---

*by [muraya.ma](https://muraya.ma)*
