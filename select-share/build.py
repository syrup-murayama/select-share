#!/usr/bin/env python3
"""
select-share / build.py

Lightroomからexportしたフォルダを読み込み、クライアント向け
2ndセレクトHTML（スタンドアロン）を生成する。

使い方:
  python build.py <jpeg_dir> [--output ./delivery] [--title "撮影タイトル"] \
    [--min-rating 1] [--copy-images] [--no-zip]

依存:
  exiftool  (brew install exiftool)

動作:
  1. exiftool -j で JPEG メタデータを一括取得
  2. XMP:Rating でフィルタ（--min-rating 未満を除外）
  3. XMP:Subject / IPTC:Keywords でキーワードを取得
  4. delivery/ フォルダに index.html + photos/ を生成
  5. --no-zip 未指定ならzipアーカイブを作成
"""

import argparse
import colorsys
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

# ---- exiftool で読み取るフィールド ----
EXIF_FIELDS = [
    "-DateTimeOriginal",
    "-XMP:Rating",
    "-Rating",            # fallback
    "-XMP:Label",
    "-XMP:Subject",       # Lightroom キーワード
    "-IPTC:Keywords",     # IPTC キーワード（fallback）
    "-ImageWidth",
    "-ImageHeight",
    "-Make",
    "-Model",
]


def run_exiftool(jpeg_dir: Path) -> list[dict]:
    """exiftool -j で metadata を一括取得する。"""
    cmd = ["exiftool", "-j", "-r", "-ext", "jpg", "-ext", "jpeg",
           "-ext", "JPG", "-ext", "JPEG"] + EXIF_FIELDS + [str(jpeg_dir.resolve())]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: exiftool 失敗: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: exiftool 出力のパース失敗: {e}", file=sys.stderr)
        sys.exit(1)


def parse_rating(meta: dict) -> int:
    """XMP:Rating → Rating の優先順で星レーティングを取得。-1=リジェクト, 0=未設定。"""
    for key in ("XMP:Rating", "Rating"):
        val = meta.get(key)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return 0


def parse_keywords(meta: dict) -> list[str]:
    """XMP:Subject (Lightroom) / IPTC:Keywords を統合してユニークなリストを返す。
    exiftool -j は namespace prefix を除いた短縮名で返すため両方チェック。
    """
    keywords = []
    for key in ("XMP:Subject", "Subject", "IPTC:Keywords", "Keywords"):
        val = meta.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            keywords.extend(str(v) for v in val)
        else:
            keywords.append(str(val))
    # 重複排除・順序保持
    seen = set()
    result = []
    for kw in keywords:
        kw = kw.strip()
        if kw and kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


def parse_datetime(meta: dict) -> str:
    """DateTimeOriginal を ISO 8601 形式に変換。例: '2026:03:31 14:30:00' → '2026-03-31T14:30:00'"""
    raw = meta.get("DateTimeOriginal", "")
    if not raw:
        return ""
    try:
        dt = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
        return dt.isoformat()
    except ValueError:
        return ""


def assign_groups(photos: list[dict], threshold_seconds: int = 3) -> None:
    """Assign group numbers based on capture time proximity."""
    group = 0
    prev_dt = None
    for p in photos:
        dt_str = p.get("datetime", "")
        if dt_str:
            try:
                dt = datetime.fromisoformat(dt_str)
                if prev_dt is None or (dt - prev_dt).total_seconds() > threshold_seconds:
                    group += 1
                prev_dt = dt
            except ValueError:
                pass
        p["group"] = group


def build_photo_db(exif_data: list[dict], min_rating: int) -> list[dict]:
    """exiftool の出力から写真DBを構築する。"""
    photos = []
    for meta in exif_data:
        rating = parse_rating(meta)
        if rating < min_rating:
            continue
        if rating == -1:   # Lightroom Reject は除外
            continue

        src_path = Path(meta.get("SourceFile", ""))
        if not src_path.exists():
            continue

        photos.append({
            "stem":     src_path.stem,
            "filename": src_path.name,
            "src":      str(src_path),
            "rating":   rating,
            "label":    meta.get("XMP:Label", "") or "",
            "keywords": parse_keywords(meta),
            "datetime": parse_datetime(meta),
            "width":    meta.get("ImageWidth", 0),
            "height":   meta.get("ImageHeight", 0),
            "camera":   " ".join(filter(None, [
                meta.get("Make", ""), meta.get("Model", "")
            ])).strip(),
        })

    # 撮影日時でソート
    photos.sort(key=lambda p: (p["datetime"], p["stem"]))
    return photos


def copy_images(photos: list[dict], photos_dir: Path, move: bool = False) -> None:
    """photos/ フォルダに JPEG をコピー（または移動）する。"""
    photos_dir.mkdir(parents=True, exist_ok=True)
    verb = "移動" if move else "コピー"
    for i, p in enumerate(photos, 1):
        src = Path(p["src"])
        dst = photos_dir / p["filename"]
        if move:
            shutil.move(str(src), dst)
        else:
            shutil.copy2(src, dst)
        if i % 50 == 0 or i == len(photos):
            print(f"  {verb}中: {i}/{len(photos)}")


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    """#rrggbb を (r, g, b) に変換する。"""
    v = h.strip()
    if len(v) != 7 or not v.startswith("#"):
        raise ValueError(f"invalid hex color: {h}")
    return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))


def _rgb_to_hex(r, g, b) -> str:
    """(r, g, b) を #rrggbb に変換する。"""
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def derive_theme(key_color: str) -> str:
    """HLS色空間でキーカラーからテーマCSSを導出する。"""
    r, g, b = _hex_to_rgb(key_color)
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)

    def with_hls(*, lightness: float, saturation: float) -> str:
        rr, gg, bb = colorsys.hls_to_rgb(
            h,
            max(0.0, min(1.0, lightness)),
            max(0.0, min(1.0, saturation)),
        )
        return _rgb_to_hex(round(rr * 255), round(gg * 255), round(bb * 255))

    accent = key_color
    accent_dark = with_hls(lightness=l - 0.09, saturation=s)
    accent_darker = with_hls(lightness=l - 0.18, saturation=s)
    bg = with_hls(lightness=0.96, saturation=min(s, 0.12))
    bg_input = with_hls(lightness=0.98, saturation=min(s, 0.07))
    text = with_hls(lightness=0.13, saturation=min(s * 0.65, 0.40))
    border = with_hls(lightness=0.82, saturation=min(s * 0.40, 0.25))
    border_light = with_hls(lightness=0.88, saturation=min(s * 0.32, 0.20))

    return f""":root {{
  --c-accent: {accent};
  --c-accent-dark: {accent_dark};
  --c-accent-darker: {accent_darker};
  --c-accent-rgb: {r}, {g}, {b};
  --c-bg: {bg};
  --c-bg-input: {bg_input};
  --c-text: {text};
  --c-border: {border};
  --c-border-light: {border_light};
}}"""


THEMES: dict[str, str] = {
    "default": """:root {
  --c-accent: #4a4a4a;
  --c-accent-dark: #333333;
  --c-accent-darker: #1d1d1d;
  --c-accent-rgb: 74, 74, 74;
  --c-bg: #f5f5f5;
  --c-bg-input: #fafafa;
  --c-text: #1a1a1a;
  --c-border: #d4d4d4;
  --c-border-light: #e0e0e0;
}""",
    "natural": "",
    "navy": """:root {
  --c-accent: #3a6ea8;
  --c-accent-dark: #285689;
  --c-accent-darker: #1c416d;
  --c-accent-rgb: 58, 110, 168;
  --c-bg: #f0f4f8;
  --c-bg-input: #f6f8fb;
  --c-text: #1a2533;
  --c-border: #c8d4e0;
  --c-border-light: #d8e4ed;
}""",
    "gold": """:root {
  --c-accent: #b8922a;
  --c-accent-dark: #987619;
  --c-accent-darker: #795c10;
  --c-accent-rgb: 184, 146, 42;
  --c-bg: #faf7f0;
  --c-bg-input: #fcfaf5;
  --c-text: #2d2418;
  --c-border: #ddd0b8;
  --c-border-light: #e8dfc8;
}""",
    "key-color": derive_theme("#9d342b"),
}


def generate_readme(title: str) -> str:
    return f"""====================================================
  セレクトツール ご利用ガイド  —  {title}
====================================================

【はじめに】
index.html をダブルクリックしてブラウザで開いてください。
Chrome または Safari を推奨します。
インターネット接続は不要です。


----------------------------------------------------
  ⚠️  作業内容の保存について（重要）
----------------------------------------------------

採用・レーティング・タグ・メモはブラウザに自動保存されますが、
以下の操作を行うとデータが消えます。

  × ブラウザの「閲覧データを消去（キャッシュ・Cookieの削除）」
  × シークレット / プライベートモードで開いて閉じたとき
  × index.html を別フォルダへ移動・リネームしたとき
  × ブラウザを再インストールしたとき

★ 作業の節目ごとに「💾 保存」ボタンで JSON ファイルを
  必ずダウンロードしてください。
  別のパソコンで再開する際は「📂 読み込み」で復元できます。


----------------------------------------------------
  基本的な使い方
----------------------------------------------------

▼ 写真を拡大する
  サムネイルをクリックすると全画面で確認できます。
  左右の矢印ボタン（または ← → キー）で前後に移動できます。

▼ 採用する
  サムネイル右上の「○」ボタン、または拡大表示中の「採用する」ボタンを押します。
  採用済みの写真は枠が緑色になります。もう一度押すと解除できます。
  ★ 絞り込み後に「表示中N枚を採用」ボタン（または Cmd/Ctrl+A）で一括採用できます。

▼ レーティングをつける
  各カード下部の ★ をクリックして 1〜5 段階で評価できます。
  同じ ★ をもう一度クリックするとリセットされます。

▼ メモを書く
  各カード下部のテキストエリアに自由にメモを入力できます。

▼ タグをつける
  「+ タグ」ボタンからプリセットまたはカスタムタグを追加できます。
  タグは検索にも使えます。


----------------------------------------------------
  表示の切り替え
----------------------------------------------------

ヘッダー右上のトグルボタンで表示モードを切り替えられます。

  [一覧]      全写真をグリッドで表示
  [グループ]  撮影時刻が近い写真をグループにまとめて表示
  [採用のみ]  採用フラグをつけた写真だけを表示

▼ グループ間隔
  グループモードに切り替えると「グループ間隔 __ 秒」の入力欄が表示されます。
  数値を変えると、その秒数以上の間隔がある写真を別グループとして自動分類します。
  （デフォルト: 3秒）


----------------------------------------------------
  絞り込み・検索
----------------------------------------------------

▼ 検索ボックス
  ファイル名・タグ・メモ・キーワードで絞り込めます。

▼ 絞り込みメニュー
  レーティング（不等号で以上/以下/一致を選択）で絞り込めます。

▼ 並び替えメニュー
  撮影時刻・撮影者レーティング・あなたのレーティングで並び替えられます。

▼ サムネイルサイズ
  スライダーで写真の表示サイズを変更できます。


----------------------------------------------------
  キーボードショートカット
----------------------------------------------------

【グリッド表示中】
  ← →            カードを移動
  Space / Enter   選択中の写真を拡大表示
  A               採用 / 解除
  1 〜 5          レーティング（再押しで解除）

【拡大表示中】
  ← →            前後の写真へ移動
  A               採用 / 解除
  1 〜 5          レーティング（再押しで解除）
  Space / Esc     閉じる


----------------------------------------------------
  採用リストを出力する
----------------------------------------------------

画面右下の「採用リストを出力」ボタンを押すと、
採用した写真のファイル名・レーティング・タグ・メモを
テキストファイルで書き出せます。


====================================================
"""


def generate_html(photos: list[dict], title: str, photos_prefix: str = "photos", extra_css: str = "", group_threshold: int = 3, credit: str = "") -> str:
    """クライアント向けスタンドアロン HTML を生成する。"""
    # 各写真の url を相対パスに変換
    photos_for_js = []
    for p in photos:
        photos_for_js.append({
            "stem":     p["stem"],
            "url":      f"{photos_prefix}/{quote(p['filename'])}",
            "rating":   p["rating"],
            "label":    p["label"],
            "keywords": p["keywords"],
            "datetime": p["datetime"],
            "camera":   p["camera"],
            "group":    p.get("group", 0),
        })

    photos_json  = json.dumps(photos_for_js, ensure_ascii=False)
    n_total      = len(photos)
    all_keywords = sorted(set(kw for p in photos for kw in p["keywords"]))
    keywords_json = json.dumps(all_keywords, ensure_ascii=False)
    import hashlib, datetime as _dt
    _stems = "".join(p["stem"] for p in photos_for_js)
    session_id = hashlib.md5(_stems.encode()).hexdigest()[:8]

    import html as _html
    import re as _re
    title_esc = _html.escape(title)
    title_slug = _re.sub(r'[/\\:*?"<>|\s]+', '-', title)
    title_slug = _re.sub(r'-+', '-', title_slug).strip('-')
    credit_name_esc = _html.escape(credit)
    if credit_name_esc:
        credit_block = f'<p class="help-prose">掲載写真はすべて <strong>© {credit_name_esc}</strong> の著作物です。無断転用・二次配布を禁じます。</p>'
    else:
        credit_block = ''
    custom_css_block = ("\n/* テーマ / カスタム */\n" + extra_css.strip()) if extra_css.strip() else ""

    return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title_esc}</title>
<style>
:root {{
  --c-accent: #7d9b6a;
  --c-accent-dark: #6a8a58;
  --c-accent-darker: #5b774c;
  --c-accent-rgb: 125, 155, 106;
  --c-bg: #f8f5f0;
  --c-bg-input: #faf8f5;
  --c-text: #3d3530;
  --c-border: #ddd5cc;
  --c-border-light: #e5ddd5;
}}
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "游ゴシック", YuGothic, sans-serif;
  background: var(--c-bg);
  color: var(--c-text);
  min-height: 100vh;
}}

/* ---- Header ---- */
.header {{
  position: sticky; top: 0; z-index: 200;
  background: rgba(255,255,255,0.97);
  border-bottom: 1px solid var(--c-border-light);
  padding: 14px 20px 10px;
  box-shadow: 0 2px 16px rgba(61,53,48,0.07);
  backdrop-filter: blur(8px);
}}
.header-top {{
  display: flex; align-items: center; gap: 14px;
  margin-bottom: 12px; flex-wrap: wrap;
}}
.site-title {{
  font-size: 1.1rem; font-weight: 700; color: var(--c-text);
  flex: 1; letter-spacing: -0.01em;
}}
.export-hdr-btn {{ white-space: nowrap; }}
.header-actions {{
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}}
.header-action-btn {{
  border: 1.5px solid var(--c-border); border-radius: 10px;
  background: var(--c-bg-input); color: #5f5148;
  padding: 8px 12px; font-size: 0.8rem; font-weight: 600;
  cursor: pointer; transition: border-color 0.15s, transform 0.15s, box-shadow 0.15s;
}}
.header-action-btn:hover {{
  border-color: var(--c-accent); color: var(--c-accent);
  transform: translateY(-1px); box-shadow: 0 3px 10px rgba(var(--c-accent-rgb), 0.12);
}}
.header-action-btn.primary {{
  background: linear-gradient(135deg, var(--c-accent), var(--c-accent-dark));
  border-color: var(--c-accent-dark); color: #fff;
  box-shadow: 0 2px 8px rgba(var(--c-accent-rgb), 0.24);
}}
.header-action-btn.primary:hover {{
  border-color: var(--c-accent-darker); color: #fff;
  box-shadow: 0 5px 14px rgba(var(--c-accent-rgb), 0.28);
}}
.last-saved {{
  font-size: 0.74rem; color: #a09080; white-space: nowrap;
  min-width: 110px; text-align: right;
}}

/* ---- Controls ---- */
.controls {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
.search-wrap {{ position: relative; }}
.search-icon {{
  position: absolute; left: 10px; top: 50%; transform: translateY(-50%);
  color: #c0b0a0; font-size: 0.8rem; pointer-events: none;
}}
.search-box {{
  padding: 7px 12px 7px 30px;
  border: 1.5px solid var(--c-border); border-radius: 20px;
  background: var(--c-bg-input); font-size: 0.83rem; color: var(--c-text);
  outline: none; width: 185px;
  transition: border-color 0.15s, box-shadow 0.15s;
}}
.search-box:focus {{ border-color: var(--c-accent); box-shadow: 0 0 0 3px rgba(var(--c-accent-rgb), 0.12); }}
.search-box::placeholder {{ color: #c0b0a0; }}
.filter-group {{ display: flex; gap: 6px; align-items: center; }}
.filter-label {{ font-size: 0.72rem; color: #a09080; white-space: nowrap; }}
select.ctrl {{
  padding: 6px 10px; border: 1.5px solid var(--c-border); border-radius: 8px;
  background: var(--c-bg-input); font-size: 0.8rem; color: var(--c-text);
  cursor: pointer; outline: none; transition: border-color 0.15s;
}}
select.ctrl:focus {{ border-color: var(--c-accent); }}
.filter-stars {{ display: flex; gap: 2px; align-items: center; }}
.bulk-btn {{
  border: 1.5px solid var(--c-border); border-radius: 8px;
  background: var(--c-bg-input); color: #5f5148;
  padding: 6px 10px; font-size: 0.78rem; font-weight: 600;
  cursor: pointer; white-space: nowrap;
  transition: border-color 0.15s, color 0.15s, box-shadow 0.15s;
}}
.bulk-btn:hover {{ border-color: var(--c-accent); color: var(--c-accent); }}
.bulk-btn:disabled {{ opacity: 0.38; cursor: default; pointer-events: none; }}
.bulk-btn.adopt {{ background: linear-gradient(135deg, var(--c-accent), var(--c-accent-dark)); border-color: var(--c-accent-dark); color: #fff; }}
.filter-star-btn {{
  background: none; border: none; padding: 0 1px; font-size: 1.05rem;
  cursor: pointer; color: var(--c-border); line-height: 1; transition: color 0.12s, transform 0.1s;
}}
.filter-star-btn.on  {{ color: var(--c-accent); }}
.filter-star-btn:hover {{ color: var(--c-accent); transform: scale(1.15); }}
.size-row {{ display: flex; align-items: center; gap: 6px; }}
.size-label {{ font-size: 0.72rem; color: #a09080; white-space: nowrap; }}
.size-slider {{
  -webkit-appearance: none; appearance: none;
  width: 80px; height: 4px; border-radius: 2px;
  background: #d8d0c8; outline: none; cursor: pointer;
}}
.size-slider::-webkit-slider-thumb {{
  -webkit-appearance: none; appearance: none;
  width: 14px; height: 14px; border-radius: 50%;
  background: var(--c-accent); cursor: pointer;
  box-shadow: 0 1px 4px rgba(0,0,0,0.2);
}}
.showing-count {{ font-size: 0.77rem; color: #b0a090; white-space: nowrap; margin-left: auto; }}

/* ---- Keyword chips ---- */
.kw-bar {{
  display: flex; flex-wrap: wrap; gap: 5px; padding: 10px 20px 8px;
  border-bottom: 1px solid #f0ebe4; background: #fdfaf7;
}}
.kw-chip {{
  padding: 3px 11px; border-radius: 12px;
  border: 1.5px solid var(--c-border); background: transparent;
  font-size: 0.75rem; color: #7d6e65; cursor: pointer;
  transition: all 0.15s;
}}
.kw-chip:hover {{ border-color: var(--c-accent); color: var(--c-accent); }}
.kw-chip.active {{ background: var(--c-accent); border-color: var(--c-accent); color: #fff; font-weight: 600; }}

/* ---- Grid ---- */
.main {{ padding: 18px 20px; }}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--thumb-size,260px), 1fr));
  gap: 14px;
}}

/* ---- Card ---- */
.card {{
  background: #fff; border: 1.5px solid #ece5dd;
  border-radius: 3px; overflow: hidden;
  transition: box-shadow 0.2s, border-color 0.2s;
}}
.card:hover {{ box-shadow: 0 4px 20px rgba(61,53,48,0.13); }}
.card.is-adopted {{
  border-color: var(--c-accent);
  box-shadow: 0 0 0 2px rgba(var(--c-accent-rgb), 0.18), 0 4px 16px rgba(var(--c-accent-rgb), 0.12);
}}
.thumb-wrap {{
  position: relative; overflow: hidden;
  aspect-ratio: 1; background: #f0ebe4; cursor: zoom-in;
}}
.thumb-wrap img {{
  width: 100%; height: 100%; object-fit: cover; object-position: center;
  display: block;
}}

/* 採用ボタン */
.adopt-btn {{
  position: absolute; top: 8px; right: 8px;
  width: 34px; height: 34px; border-radius: 50%;
  border: none; cursor: pointer;
  background: rgba(255,255,255,0.88);
  display: flex; align-items: center; justify-content: center;
  font-size: 1rem; font-weight: 700;
  transition: all 0.2s; backdrop-filter: blur(4px);
  box-shadow: 0 2px 8px rgba(0,0,0,0.12);
  color: #9d8e85; opacity: 0;
}}
.card:hover .adopt-btn, .card.is-adopted .adopt-btn {{ opacity: 1; }}
.adopt-btn:hover {{ transform: scale(1.12); }}
.card.is-adopted .adopt-btn {{ background: var(--c-accent); color: #fff; box-shadow: 0 2px 8px rgba(var(--c-accent-rgb), 0.4); }}

/* 撮影者レーティングバッジ */
.rating-badge {{
  position: absolute; bottom: 7px; left: 8px;
  padding: 2px 8px; border-radius: 8px;
  background: rgba(0,0,0,0.38); backdrop-filter: blur(4px);
  font-size: 0.72rem; color: #f5c842; letter-spacing: 1px;
}}

/* カード本体 */
.card-body {{ padding: 10px 12px 12px; }}
.card-name {{
  font-size: 0.7rem; color: #b0a090;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  margin-bottom: 7px;
}}
.ratings-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
.cam-block, .cli-block {{ display: flex; align-items: center; gap: 4px; }}
.rating-label {{ font-size: 0.62rem; color: #b0a090; white-space: nowrap; }}
.cam-stars {{ color: #c8a45d; font-size: 0.9rem; letter-spacing: 1px; }}
.rating-sep {{ color: var(--c-border); font-size: 0.6rem; }}
.star-picker {{ display: flex; gap: 1px; }}
.star-pick-btn {{
  background: none; border: none; cursor: pointer;
  font-size: 1.05rem; padding: 0 1px; line-height: 1;
  color: #d8d0c8; transition: color 0.1s, transform 0.1s;
}}
.star-pick-btn:hover {{ transform: scale(1.25); }}
.star-pick-btn.on {{ color: var(--c-accent); }}
.star-reset-btn {{
  background: none; border: none; cursor: pointer;
  font-size: 0.65rem; color: #c0b0a0; padding: 0 2px;
}}
.star-reset-btn:hover {{ color: #9d8e85; }}

/* キーワード（カード内） */
.card-keywords {{ display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 7px; }}
.card-kw {{
  padding: 1px 7px; border-radius: 8px;
  background: #f0ebe4; border: 1px solid #ddd5c8;
  font-size: 0.63rem; color: #7d6e65;
}}

/* タグ行 */
.tags-row {{ display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 7px; min-height: 22px; }}
.tag {{
  display: inline-flex; align-items: center; gap: 3px;
  padding: 2px 8px; border-radius: 10px;
  background: #e8f0e3; border: 1px solid #c8d8be;
  font-size: 0.67rem; color: #5a7a50; cursor: pointer;
  transition: all 0.15s;
}}
.tag:hover {{ background: #d8ecce; }}
.tag-x {{ font-size: 0.6rem; color: #8aaa80; }}
.add-tag-btn {{
  padding: 2px 8px; border-radius: 10px;
  background: transparent; border: 1px dashed #c8b8a8;
  font-size: 0.67rem; color: #c0b0a0; cursor: pointer;
  transition: all 0.15s;
}}
.add-tag-btn:hover {{ border-color: var(--c-accent); color: var(--c-accent); }}

/* メモ */
.note-field {{
  width: 100%; padding: 5px 8px;
  border: 1px solid #ece5dd; border-radius: 7px;
  background: var(--c-bg-input); font-size: 0.72rem;
  font-family: inherit; color: var(--c-text);
  resize: none; outline: none; line-height: 1.45; min-height: 36px;
  transition: border-color 0.15s;
}}
.note-field:focus {{ border-color: var(--c-accent); }}
.note-field::placeholder {{ color: #c8b8a8; font-style: italic; }}

/* ---- 写真モーダル ---- */
.modal {{
  display: none; position: fixed; inset: 0;
  background: rgba(30,22,18,0.90); z-index: 1000;
  align-items: center; justify-content: center;
  flex-direction: column; gap: 14px; cursor: zoom-out;
}}
.modal.open {{ display: flex; }}
.modal-img {{
  max-width: 92vw; max-height: 82vh;
  border-radius: 8px; object-fit: contain;
  box-shadow: 0 12px 48px rgba(0,0,0,0.5);
}}
.modal-nav {{
  position: absolute; top: 50%; transform: translateY(-50%);
  width: 52px; height: 52px; border-radius: 50%;
  border: none; background: rgba(255,255,255,0.14);
  color: #f8f5f0; font-size: 2rem; line-height: 1; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.15s, opacity 0.15s;
}}
.modal-nav:hover {{ background: rgba(255,255,255,0.24); }}
.modal-nav:disabled {{ opacity: 0.28; cursor: default; }}
.modal-nav.prev {{ left: 18px; }}
.modal-nav.next {{ right: 18px; }}
.modal-footer {{
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap; justify-content: center;
}}
.modal-name {{ color: #c8b8a8; font-size: 0.82rem; }}
.modal-close {{
  position: absolute; top: 14px; right: 18px;
  background: none; border: none; color: #a09080; font-size: 2rem; cursor: pointer;
}}
.modal-close:hover {{ color: #f8f5f0; }}
.modal-adopt-btn {{
  padding: 8px 22px; border-radius: 20px;
  border: 2px solid var(--c-accent); background: transparent;
  color: var(--c-accent); font-size: 0.88rem; font-weight: 600; cursor: pointer;
  transition: all 0.2s;
}}
.modal-adopt-btn.on, .modal-adopt-btn:hover {{ background: var(--c-accent); color: #fff; }}

/* ---- タグモーダル ---- */
.tag-modal {{
  display: none; position: fixed; inset: 0;
  background: rgba(30,22,18,0.70); z-index: 1100;
  align-items: center; justify-content: center;
}}
.tag-modal.open {{ display: flex; }}
.tag-modal-box {{
  background: #fff; border-radius: 16px; padding: 22px 24px;
  width: 380px; max-width: 92vw;
  box-shadow: 0 12px 48px rgba(0,0,0,0.2);
}}
.tag-modal-title {{ font-size: 0.9rem; font-weight: 700; color: var(--c-text); margin-bottom: 14px; }}
.tag-section-label {{ font-size: 0.7rem; color: #a09080; margin-bottom: 7px; font-weight: 600; letter-spacing: 0.04em; }}
.tag-presets {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }}
.tag-preset-btn {{
  padding: 5px 12px; border-radius: 14px;
  border: 1.5px solid var(--c-border); background: transparent;
  font-size: 0.78rem; color: #7d6e65; cursor: pointer; transition: all 0.15s;
}}
.tag-preset-btn:hover {{ border-color: var(--c-accent); color: var(--c-accent); }}
.tag-preset-btn.on {{ background: var(--c-accent); border-color: var(--c-accent); color: #fff; }}
.tag-custom-row {{ display: flex; gap: 6px; margin-bottom: 14px; }}
.tag-custom-input {{
  flex: 1; padding: 7px 10px; border: 1.5px solid var(--c-border); border-radius: 8px;
  font-size: 0.82rem; color: var(--c-text); outline: none; transition: border-color 0.15s;
}}
.tag-custom-input:focus {{ border-color: var(--c-accent); }}
.tag-custom-input::placeholder {{ color: #c0b0a0; }}
.tag-add-btn {{
  padding: 7px 14px; border-radius: 8px; border: none;
  background: var(--c-accent); color: #fff; font-size: 0.82rem; font-weight: 600; cursor: pointer;
}}
.tag-add-btn:hover {{ background: var(--c-accent-dark); }}
.tag-modal-close {{
  padding: 7px 18px; border-radius: 8px;
  border: 1.5px solid var(--c-border); background: transparent;
  color: #7d6e65; font-size: 0.82rem; cursor: pointer;
}}
.tag-modal-close:hover {{ border-color: #c0b0a0; }}
.tag-modal-footer {{ display: flex; justify-content: flex-end; }}

/* ---- タグ集計ドロップダウン ---- */
.tag-summary-wrap {{ position: relative; }}
.coll-panel {{
  display: none; position: absolute; top: calc(100% + 6px); right: 0;
  background: rgba(255,255,255,0.98); backdrop-filter: blur(8px);
  border: 1.5px solid var(--c-border-light); border-radius: 14px;
  padding: 14px 16px; min-width: 190px;
  box-shadow: 0 6px 24px rgba(61,53,48,0.10); z-index: 200;
}}
.coll-panel.open {{ display: block; }}
.coll-title {{ font-size: 0.72rem; font-weight: 700; color: var(--c-accent); margin-bottom: 10px; letter-spacing: 0.04em; text-transform: uppercase; }}
.coll-rows {{ display: flex; flex-direction: column; gap: 4px; max-height: 200px; overflow-y: auto; }}
.coll-row {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; cursor: pointer; padding: 2px 0; }}
.coll-tag {{ font-size: 0.72rem; color: #7d6e65; }}
.coll-n {{ font-size: 0.78rem; font-weight: 700; color: var(--c-text); }}
.coll-row:hover .coll-tag {{ color: var(--c-accent); }}

/* ---- キーボードヒント（ヘッダー内） ---- */
.kbd-hint {{
  display: flex; gap: 10px; align-items: center;
  margin-left: auto; flex-shrink: 0;
  font-size: 0.68rem; color: #a09080;
}}
.kbd-hint-item {{ display: flex; align-items: center; gap: 3px; white-space: nowrap; }}
kbd {{
  display: inline-block; padding: 1px 6px;
  border: 1px solid var(--c-border); border-radius: 4px;
  background: var(--c-bg-input); font-family: inherit;
  font-size: 0.65rem; color: var(--c-text);
  box-shadow: 0 1px 2px rgba(0,0,0,0.08);
}}

/* ---- グリッドフォーカス ---- */
.card.is-focused {{
  outline: 3px solid var(--c-accent);
  outline-offset: 2px;
}}

/* ---- モーダルレーティング ---- */
.modal-rating {{
  color: var(--c-accent); font-size: 0.82rem; letter-spacing: 1px; min-width: 4ch;
}}

/* ---- ショートカット一覧（ページ下部） ---- */
.shortcut-footer {{
  max-width: 900px; margin: 32px auto 40px;
  padding: 20px 24px;
  background: var(--c-bg-input); border: 1px solid var(--c-border-light);
  border-radius: 8px;
  display: flex; gap: 32px; flex-wrap: wrap;
}}
.shortcut-col {{ flex: 1; min-width: 180px; }}
.shortcut-col-title {{
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em;
  color: var(--c-accent); text-transform: uppercase; margin-bottom: 10px;
}}
.shortcut-row {{
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px; font-size: 0.75rem; color: var(--c-text);
}}
.shortcut-keys {{ display: flex; gap: 3px; flex-shrink: 0; }}

/* ---- 保存警告バナー ---- */
.save-warning {{
  background: #fffbeb; border-bottom: 2px solid #f59e0b;
  padding: 12px 20px; display: flex; align-items: flex-start; gap: 12px;
  font-size: 0.82rem; color: #78350f; line-height: 1.55;
}}
.save-warning-icon {{ font-size: 1.4rem; flex-shrink: 0; margin-top: 1px; }}
.save-warning-body {{ flex: 1; }}
.save-warning-title {{ font-weight: 700; font-size: 0.88rem; margin-bottom: 4px; }}
.save-warning-text {{ color: #92400e; }}
.save-warning-text strong {{ color: #b45309; }}
.save-warning-actions {{ display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }}
.save-warning-btn {{
  padding: 6px 16px; border-radius: 8px; border: none; cursor: pointer;
  font-size: 0.8rem; font-weight: 700;
  background: #f59e0b; color: #fff;
  transition: background 0.15s;
}}
.save-warning-btn:hover {{ background: #d97706; }}
.save-warning-dismiss {{
  background: none; border: 1px solid #d97706; color: #92400e;
  padding: 5px 12px; border-radius: 8px; cursor: pointer;
  font-size: 0.78rem; transition: all 0.15s;
}}
.save-warning-dismiss:hover {{ background: #fef3c7; }}

/* ---- View toggle ---- */
.view-toggle {{
  display: flex; border: 1.5px solid var(--c-border); border-radius: 10px; overflow: hidden; flex-shrink: 0;
}}
.view-toggle-btn {{
  border: none; background: var(--c-bg-input); color: #7d6e65;
  padding: 7px 14px; font-size: 0.8rem; font-weight: 600; cursor: pointer;
  transition: background 0.15s, color 0.15s; white-space: nowrap;
}}
.view-toggle-btn + .view-toggle-btn {{ border-left: 1px solid var(--c-border); }}
.view-toggle-btn.active {{ background: var(--c-accent); color: #fff; }}

/* ---- Group mode ---- */
.grid.is-group-mode {{ display: block; }}
.group-section {{ margin-bottom: 28px; }}
.group-header {{
  font-size: 0.72rem; color: #a09080; font-weight: 700;
  padding: 4px 0 8px; border-bottom: 1px solid var(--c-border-light);
  margin-bottom: 12px; letter-spacing: 0.04em;
}}
.group-row {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--thumb-size,260px), 1fr));
  gap: 14px;
}}

/* ---- Modal keyboard hints ---- */
.modal-keys {{
  display: flex; gap: 10px; flex-wrap: wrap; justify-content: center;
  font-size: 0.68rem; color: rgba(255,255,255,0.45);
}}
.modal-keys kbd {{
  background: rgba(255,255,255,0.12); border-color: rgba(255,255,255,0.25);
  color: rgba(255,255,255,0.75); box-shadow: none;
}}
.mk-item {{ display: flex; align-items: center; gap: 3px; white-space: nowrap; }}

/* ---- ヘルプモーダル ---- */
.help-modal {{
  display: none; position: fixed; inset: 0; z-index: 500;
  background: rgba(40,32,28,0.72); backdrop-filter: blur(4px);
  align-items: center; justify-content: center;
}}
.help-modal.open {{ display: flex; }}
.help-modal-box {{
  position: relative;
  background: var(--c-bg); border: 1.5px solid var(--c-border-light);
  border-radius: 18px; padding: 32px 36px; max-width: 580px; width: 92%;
  max-height: 85vh; overflow-y: auto;
  box-shadow: 0 12px 48px rgba(40,32,28,0.22);
}}
.help-modal-title {{
  font-size: 1.05rem; font-weight: 700; color: var(--c-text); margin-bottom: 22px;
}}
.help-modal-close {{
  position: absolute; top: 16px; right: 20px;
  background: none; border: none; font-size: 1.2rem;
  color: var(--c-text-muted); cursor: pointer; line-height: 1;
}}
.help-section-title {{
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.08em;
  color: var(--c-accent); text-transform: uppercase; margin: 18px 0 8px;
}}
.help-section-title:first-of-type {{ margin-top: 0; }}
.help-row {{
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 7px; font-size: 0.8rem; color: var(--c-text);
}}
.help-keys {{ display: flex; gap: 3px; flex-shrink: 0; min-width: 90px; }}
.help-prose {{
  font-size: 0.8rem; color: var(--c-text); line-height: 1.6; margin-bottom: 6px;
}}

@media (max-width: 640px) {{
  .main {{ padding: 12px; }}
  .kbd-hint {{ display: none; }}
  .shortcut-footer {{ flex-direction: column; gap: 20px; }}
}}
{custom_css_block}
</style>
</head>
<body>

<div class="save-warning" id="save-warning">
  <div class="save-warning-icon">⚠️</div>
  <div class="save-warning-body">
    <div class="save-warning-title">作業データの消失を防ぐため、定期的に保存してください</div>
    <div class="save-warning-text">
      採用・レーティング・タグ・メモはブラウザに自動保存されますが、
      <strong>「閲覧データを消去」・シークレットモード・ファイルの移動</strong> などで消えることがあります。<br>
      作業の節目ごとに <strong>「💾 保存」ボタン</strong> で JSON ファイルをダウンロードしてください。別のパソコンでも「📂 読み込み」で再開できます。
    </div>
    <div class="save-warning-actions">
      <button class="save-warning-btn" onclick="exportState()">💾 今すぐ保存する</button>
      <button class="save-warning-dismiss" onclick="dismissWarning()">理解しました・非表示にする</button>
    </div>
  </div>
</div>

<div class="header">
  <div class="header-top">
    <div class="site-title">{title_esc}</div>
    <div class="header-actions">
      <button class="header-action-btn primary" onclick="exportState()">💾 保存</button>
      <button class="header-action-btn" onclick="triggerImport()">📂 読み込み</button>
      <button class="header-action-btn" onclick="openHelp()" title="使い方">？</button>
      <span class="last-saved" id="last-saved-label"></span>
      <input type="file" id="state-file-input" accept="application/json,.json" style="display:none" onchange="importState(event)">
    </div>
    <div class="view-toggle">
      <button class="view-toggle-btn active" id="vt-list"    onclick="setViewMode('list')">一覧</button>
      <button class="view-toggle-btn"        id="vt-group"   onclick="setViewMode('group')">グループ</button>
      <button class="view-toggle-btn"        id="vt-adopted" onclick="setViewMode('adopted')">採用のみ</button>
    </div>
    <div class="tag-summary-wrap">
      <button class="header-action-btn" onclick="toggleCollPanel(event)">🏷 タグ集計</button>
      <div class="coll-panel" id="coll-panel">
        <div class="coll-title">タグ集計</div>
        <div class="coll-rows" id="coll-rows">
          <div style="font-size:0.72rem;color:#c0b0a0">タグなし</div>
        </div>
      </div>
    </div>
    <button class="header-action-btn primary export-hdr-btn" onclick="exportList()">
      📋 採用リストを出力（<span id="adopt-count">0</span>枚）
    </button>
  </div>
  <div class="controls">
    <div class="search-wrap">
      <span class="search-icon">🔍</span>
      <input class="search-box" id="search-box" type="search"
             placeholder="ファイル名 / タグ / メモ / キーワード…"
             oninput="onSearch(this.value)">
    </div>
    <div class="filter-group">
      <span class="filter-label">絞り込み</span>
      <select class="ctrl" id="filter-cmp" onchange="onFilterCmpChange()">
        <option value="gte">以上</option>
        <option value="lte">以下</option>
        <option value="eq">一致</option>
      </select>
      <div class="filter-stars" id="filter-stars"></div>
    </div>
    <div class="filter-group">
      <button class="bulk-btn adopt" id="bulk-adopt-btn" onclick="bulkAdoptVisible(true)">✓ 表示中を採用</button>
      <button class="bulk-btn" id="bulk-unadopt-btn" onclick="bulkAdoptVisible(false)">○ 解除</button>
    </div>
    <div class="filter-group">
      <span class="filter-label">並び替え</span>
      <select class="ctrl" id="sort-select" onchange="onSortChange()">
        <option value="dt_asc">撮影時刻 ↑</option>
        <option value="dt_desc">撮影時刻 ↓</option>
        <option value="cr_desc">撮影者レーティング ↓</option>
        <option value="cr_asc">撮影者レーティング ↑</option>
        <option value="cl_desc">あなたのレーティング ↓</option>
        <option value="cl_asc">あなたのレーティング ↑</option>
      </select>
    </div>
    <div class="size-row">
      <span class="size-label">🖼 サイズ</span>
      <input class="size-slider" id="size-slider" type="range"
             min="160" max="460" step="20" value="260"
             oninput="onSizeChange(this.value)">
    </div>
    <div class="filter-group" id="group-threshold-wrap" style="display:none">
      <span class="filter-label">グループ間隔</span>
      <input class="ctrl" id="group-threshold-input" type="number"
             min="1" max="3600" value="{group_threshold}" style="width:58px;text-align:right"
             oninput="onGroupThresholdChange(this.value)">
      <span class="filter-label">秒</span>
    </div>
    <span class="showing-count" id="showing-count">{n_total} 枚</span>
    <div class="kbd-hint">
      <div class="kbd-hint-item"><kbd>←</kbd><kbd>→</kbd><span>移動</span></div>
      <div class="kbd-hint-item"><kbd>Space</kbd><span>拡大</span></div>
      <div class="kbd-hint-item"><kbd>A</kbd><span>採用</span></div>
      <div class="kbd-hint-item"><kbd>1-5</kbd><span>レーティング</span></div>
      <div class="kbd-hint-item"><kbd>Esc</kbd><span>閉じる</span></div>
    </div>
  </div>
</div>

<!-- Lightroomキーワードチップ -->
<div class="kw-bar" id="kw-bar" style="display:none"></div>

<div class="main">
  <div class="grid" id="grid"></div>
</div>

<!-- ヘルプモーダル -->
<div class="help-modal" id="help-modal" onclick="closeHelpOutside(event)">
  <div class="help-modal-box">
    <button class="help-modal-close" onclick="closeHelp()">&#x2715;</button>
    <div class="help-modal-title">📖 使い方</div>

    <div class="help-section-title">操作方法</div>
    <p class="help-prose">写真にカーソルを合わせると採用ボタン・レーティングが表示されます。クリックまたはキーボードで操作できます。</p>

    <div class="help-section-title">キーボードショートカット — グリッド</div>
    <div class="help-row"><div class="help-keys"><kbd>←</kbd><kbd>→</kbd></div><span>カードを移動</span></div>
    <div class="help-row"><div class="help-keys"><kbd>Space</kbd><kbd>Enter</kbd></div><span>拡大表示</span></div>
    <div class="help-row"><div class="help-keys"><kbd>A</kbd></div><span>採用 / 解除</span></div>
    <div class="help-row"><div class="help-keys"><kbd>1</kbd>〜<kbd>5</kbd></div><span>レーティング（再押しで解除）</span></div>
    <div class="help-row"><div class="help-keys"><kbd>Cmd/Ctrl</kbd><kbd>A</kbd></div><span>表示中を一括採用（再押しで全解除）</span></div>

    <div class="help-section-title">キーボードショートカット — 拡大表示中</div>
    <div class="help-row"><div class="help-keys"><kbd>←</kbd><kbd>→</kbd></div><span>前後の写真へ</span></div>
    <div class="help-row"><div class="help-keys"><kbd>A</kbd></div><span>採用 / 解除</span></div>
    <div class="help-row"><div class="help-keys"><kbd>1</kbd>〜<kbd>5</kbd></div><span>レーティング（再押しで解除）</span></div>
    <div class="help-row"><div class="help-keys"><kbd>Space</kbd><kbd>Esc</kbd></div><span>閉じる</span></div>

    <div class="help-section-title">保存・読み込み</div>
    <p class="help-prose"><strong>💾 保存</strong> — セレクト状態（採用・レーティング・メモ）をJSONファイルとして保存します。<br>
    <strong>📂 読み込み</strong> — 保存したJSONを読み込んで状態を復元します。<br>
    作業途中でブラウザを閉じる前に必ず保存してください。</p>

    <div class="help-section-title">採用リストの出力</div>
    <p class="help-prose"><strong>📋 採用リストを出力</strong> — 採用した写真のファイル名一覧を .txt ファイルとしてダウンロードします。このファイルをカメラマンに送ることでセレクト結果を共有できます。</p>

    <div class="help-section-title">ご利用にあたって</div>
    {credit_block}
    <p class="help-prose">本ギャラリーはセレクト確認専用です。第三者への共有・転送はご遠慮ください。</p>
    <p class="help-prose">本ツールの利用により生じた損害について、製作者は一切の責任を負いません。</p>
    <p class="help-prose" style="margin-top:14px;font-size:0.72rem;color:var(--c-text-muted)">Generated by <a href="https://github.com/syrup-murayama/select-share" target="_blank" style="color:inherit;text-decoration:underline">select-share</a> (MIT License)</p>
  </div>
</div>

<!-- ショートカット一覧 -->
<div class="shortcut-footer">
  <div class="shortcut-col">
    <div class="shortcut-col-title">グリッド操作</div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>←</kbd><kbd>→</kbd></div><span>カードを移動</span></div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>Space</kbd><kbd>Enter</kbd></div><span>拡大表示</span></div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>A</kbd></div><span>採用 / 解除</span></div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>1</kbd>〜<kbd>5</kbd></div><span>レーティング（再押しで解除）</span></div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>⌘/Ctrl</kbd><kbd>A</kbd></div><span>表示中を一括採用（再押しで全解除）</span></div>
  </div>
  <div class="shortcut-col">
    <div class="shortcut-col-title">拡大表示中</div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>←</kbd><kbd>→</kbd></div><span>前後の写真へ</span></div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>A</kbd></div><span>採用 / 解除</span></div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>1</kbd>〜<kbd>5</kbd></div><span>レーティング（再押しで解除）</span></div>
    <div class="shortcut-row"><div class="shortcut-keys"><kbd>Space</kbd><kbd>Esc</kbd></div><span>閉じる</span></div>
  </div>
</div>

<!-- 写真モーダル -->
<div class="modal" id="modal" onclick="closeModal(event)">
  <button class="modal-close" onclick="closeModal()">&#x2715;</button>
  <button class="modal-nav prev" id="modal-prev-btn" onclick="showAdjacentModal(-1)" aria-label="前の写真">&#x2039;</button>
  <img class="modal-img" id="modal-img" src="" alt="">
  <button class="modal-nav next" id="modal-next-btn" onclick="showAdjacentModal(1)" aria-label="次の写真">&#x203A;</button>
  <div class="modal-footer">
    <span class="modal-name" id="modal-name"></span>
    <span class="modal-rating" id="modal-rating"></span>
    <button class="modal-adopt-btn" id="modal-adopt-btn" onclick="toggleAdoptModal()">採用する</button>
  </div>
  <div class="modal-keys">
    <span class="mk-item"><kbd>←</kbd><kbd>→</kbd> 移動</span>
    <span class="mk-item"><kbd>A</kbd> 採用</span>
    <span class="mk-item"><kbd>1-5</kbd> レーティング</span>
    <span class="mk-item"><kbd>Space</kbd><kbd>Esc</kbd> 閉じる</span>
  </div>
</div>

<!-- タグモーダル -->
<div class="tag-modal" id="tag-modal" onclick="closeTagModalOverlay(event)">
  <div class="tag-modal-box">
    <div class="tag-modal-title" id="tag-modal-title">タグを編集</div>
    <div class="tag-section-label">プリセット</div>
    <div class="tag-presets" id="tag-presets"></div>
    <div class="tag-section-label">カスタム</div>
    <div class="tag-custom-row">
      <input class="tag-custom-input" id="tag-custom-input"
             placeholder="タグを入力して追加…"
             onkeydown="if(event.key==='Enter')addCustomTag()">
      <button class="tag-add-btn" onclick="addCustomTag()">追加</button>
    </div>
    <div class="tag-modal-footer">
      <button class="tag-modal-close" onclick="closeTagModal()">閉じる</button>
    </div>
  </div>
</div>


<script>
/* ---- Data ---- */
const PHOTOS = {photos_json};
const ALL_KEYWORDS = {keywords_json};
const PRESET_TAGS = ['お気に入り', 'ヘービー候補', 'SNS向け', 'プリント向け', '要確認', '使わない'];
const SESSION_ID = '{session_id}';
const TITLE_SLUG = {json.dumps(title_slug)};
const STORE = 'select-share-' + SESSION_ID;
const GROUP_THRESHOLD_DEFAULT = {group_threshold};

/* ---- State ---- */
let S = {{
  ratings:  {{}},   // stem -> 1|2|3|4|5 (クライアント設定)
  adopted:  {{}},   // stem -> bool
  tags:     {{}},   // stem -> string[]
  notes:    {{}},   // stem -> string
  ui: {{ filter_cmp: 'gte', filter_level: 0, sort: 'dt_asc', thumb_size: 260, search: '', kw_filter: null, last_exported_at: '', view_mode: 'list', group_threshold: GROUP_THRESHOLD_DEFAULT }}
}};

function save() {{ try {{ localStorage.setItem(STORE, JSON.stringify(S)); }} catch(e) {{}} }}
function isPlainObject(v) {{ return !!v && typeof v === 'object' && !Array.isArray(v); }}
function mergeState(src) {{
  if (!isPlainObject(src)) return false;
  if (isPlainObject(src.ratings)) S.ratings = src.ratings;
  if (isPlainObject(src.adopted)) S.adopted = src.adopted;
  if (isPlainObject(src.tags)) S.tags = src.tags;
  if (isPlainObject(src.notes)) S.notes = src.notes;
  if (isPlainObject(src.ui)) {{
    Object.assign(S.ui, src.ui);
    // 旧 filter_op → 新 filter_cmp / filter_level へ移行
    if (S.ui.filter_op !== undefined) {{
      const op = S.ui.filter_op;
      if      (op === 'eq1')    {{ S.ui.filter_cmp = 'eq';  S.ui.filter_level = 1; }}
      else if (op === 'eq2')    {{ S.ui.filter_cmp = 'eq';  S.ui.filter_level = 2; }}
      else if (op === 'eq3')    {{ S.ui.filter_cmp = 'eq';  S.ui.filter_level = 3; }}
      else if (op === 'gte2')   {{ S.ui.filter_cmp = 'gte'; S.ui.filter_level = 2; }}
      else                      {{ S.ui.filter_level = 0; }}
      delete S.ui.filter_op;
    }}
  }}
  return true;
}}
function load() {{
  try {{
    const d = JSON.parse(localStorage.getItem(STORE) || 'null');
    if (!d) return;
    mergeState(d);
  }} catch(e) {{}}
}}
function fmtDate(d) {{
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${{y}}-${{m}}-${{day}}`;
}}
function fmtTime(v) {{
  if (!v) return '';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return '';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${{hh}}:${{mm}}`;
}}
function updateLastSavedLabel() {{
  const el = document.getElementById('last-saved-label');
  const t = fmtTime(S.ui.last_exported_at);
  el.textContent = t ? '最終保存: ' + t : '';
}}

function effRating(stem) {{
  const cr = S.ratings[stem];
  if (cr !== undefined && cr !== null) return cr;
  return (PHOTOS.find(x => x.stem === stem) || {{}}).rating || 0;
}}

/* ---- Filter / Sort ---- */
function filterAndSort() {{
  const lvl = S.ui.filter_level || 0;
  const cmp = S.ui.filter_cmp   || 'gte';
  const q = S.ui.search.toLowerCase().trim();
  const kw = S.ui.kw_filter;
  return PHOTOS.filter(p => {{
    const r = effRating(p.stem);
    if (lvl > 0) {{
      if (cmp === 'gte' && r <  lvl) return false;
      if (cmp === 'lte' && r >  lvl) return false;
      if (cmp === 'eq'  && r !== lvl) return false;
    }}
    if (S.ui.view_mode === 'adopted' && !S.adopted[p.stem]) return false;
    if (kw && !p.keywords.includes(kw)) return false;
    if (q) {{
      const inStem = p.stem.toLowerCase().includes(q);
      const inTags = (S.tags[p.stem] || []).some(t => t.toLowerCase().includes(q));
      const inNote = (S.notes[p.stem] || '').toLowerCase().includes(q);
      const inKw   = p.keywords.some(k => k.toLowerCase().includes(q));
      if (!inStem && !inTags && !inNote && !inKw) return false;
    }}
    return true;
  }}).sort((a, b) => {{
    const sort = S.ui.sort;
    if (sort === 'dt_asc')  return a.datetime < b.datetime ? -1 : a.datetime > b.datetime ? 1 : 0;
    if (sort === 'dt_desc') return b.datetime < a.datetime ? -1 : b.datetime > a.datetime ? 1 : 0;
    if (sort === 'cr_asc')  return a.rating !== b.rating ? a.rating - b.rating : (a.datetime < b.datetime ? -1 : 1);
    if (sort === 'cr_desc') return a.rating !== b.rating ? b.rating - a.rating : (a.datetime < b.datetime ? -1 : 1);
    const ca = effRating(a.stem), cb = effRating(b.stem);
    if (sort === 'cl_asc')  return ca !== cb ? ca - cb : (a.datetime < b.datetime ? -1 : 1);
    if (sort === 'cl_desc') return ca !== cb ? cb - ca : (a.datetime < b.datetime ? -1 : 1);
    return 0;
  }});
}}

/* ---- Render ---- */
function esc(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;'); }}

function renderCard(p) {{
  const ur   = S.ratings[p.stem] || 0;   // ユーザーが明示的に設定したレーティング
  const isAd = !!S.adopted[p.stem];
  const tags = S.tags[p.stem] || [];
  const note = esc(S.notes[p.stem] || '');
  const camH = p.rating > 0 ? '★'.repeat(p.rating) : '—';
  let pickH = '';
  for (let i = 1; i <= 5; i++) {{
    pickH += `<button class="star-pick-btn ${{ur >= i ? 'on' : ''}}" onclick="setRating(this.closest('.card').dataset.stem,${{i}})" title="${{i}}★">★</button>`;
  }}
  if (ur > 0) pickH += `<button class="star-reset-btn" onclick="setRating(this.closest('.card').dataset.stem,0)">✕</button>`;

  const kwH = p.keywords.map(k => `<span class="card-kw">${{esc(k)}}</span>`).join('');
  const tagsH = tags.map(t => `<span class="tag" data-tag="${{esc(t)}}" onclick="removeTag(this.closest('.card').dataset.stem,this.dataset.tag)">${{esc(t)}} <span class="tag-x">×</span></span>`).join('')
              + `<button class="add-tag-btn" onclick="openTagModal(this.closest('.card').dataset.stem)">+ タグ</button>`;
  const dt = p.datetime ? p.datetime.slice(11, 16) : '';

  return `<div class="card ${{isAd ? 'is-adopted' : ''}}" id="card-${{p.stem}}" data-stem="${{esc(p.stem)}}" data-url="${{esc(p.url)}}">
  <div class="thumb-wrap" onclick="setGridFocus(this.closest('.card').dataset.stem);openModal(this.closest('.card').dataset.url,this.closest('.card').dataset.stem)">
    <img src="${{esc(p.url)}}" alt="${{esc(p.stem)}}" loading="lazy">
    <button class="adopt-btn" onclick="toggleAdopt(event,this.closest('.card').dataset.stem)">${{isAd ? '✓' : '○'}}</button>
    <div class="rating-badge">${{camH}} ${{dt}}</div>
  </div>
  <div class="card-body">
    <div class="card-name">${{esc(p.stem)}}</div>
    <div class="ratings-row">
      <div class="cam-block"><span class="rating-label">撮影者</span><span class="cam-stars">${{camH}}</span></div>
      <span class="rating-sep">◆</span>
      <div class="cli-block"><span class="rating-label" style="color:var(--c-accent)">あなた</span><div class="star-picker">${{pickH}}</div></div>
    </div>
    ${{kwH ? `<div class="card-keywords">${{kwH}}</div>` : ''}}
    <div class="tags-row" id="tags-${{p.stem}}">${{tagsH}}</div>
    <textarea class="note-field" placeholder="メモ…" rows="1"
      onchange="setNote(this.closest('.card').dataset.stem,this.value)"
      onfocus="this.rows=3" onblur="if(!this.value.trim())this.rows=1"
    >${{note}}</textarea>
  </div>
</div>`;
}}

function regroupPhotos(thresholdSec) {{
  let group = 0, prevMs = null;
  PHOTOS.forEach(p => {{
    if (p.datetime) {{
      const ms = new Date(p.datetime).getTime();
      if (prevMs === null || (ms - prevMs) / 1000 > thresholdSec) group++;
      prevMs = ms;
    }}
    p.group = group;
  }});
}}

function renderGrouped(list) {{
  const groupMap = new Map();
  list.forEach(p => {{
    const g = p.group || 0;
    if (!groupMap.has(g)) groupMap.set(g, []);
    groupMap.get(g).push(p);
  }});
  let idx = 0, html = '';
  groupMap.forEach((items) => {{
    idx++;
    html += `<div class="group-section">
      <div class="group-header">グループ ${{idx}}（${{items.length}}枚）</div>
      <div class="group-row">${{items.map(renderCard).join('')}}</div>
    </div>`;
  }});
  return html;
}}

function renderGrid() {{
  const list = filterAndSort();
  const gridEl = document.getElementById('grid');
  const isGroup = S.ui.view_mode === 'group';
  gridEl.classList.toggle('is-group-mode', isGroup);
  if (isGroup) {{
    gridEl.innerHTML = renderGrouped(list);
  }} else {{
    gridEl.innerHTML = list.map(renderCard).join('');
  }}
  document.getElementById('showing-count').textContent = list.length + ' / ' + PHOTOS.length + ' 枚';
  const adoptBtn = document.getElementById('bulk-adopt-btn');
  const unadoptBtn = document.getElementById('bulk-unadopt-btn');
  if (adoptBtn) {{
    adoptBtn.textContent = '✓ 表示中 ' + list.length + ' 枚を採用';
    adoptBtn.disabled = list.length === 0;
    unadoptBtn.disabled = list.length === 0;
  }}
  updateAdoptCounter();
  updateCollPanel();
  if (_focusedStem) {{
    const el = document.getElementById('card-' + _focusedStem);
    if (el) el.classList.add('is-focused'); else _focusedStem = null;
  }}
}}

function updateCard(stem) {{
  const el = document.getElementById('card-' + stem);
  if (!el) return;
  const p = PHOTOS.find(x => x.stem === stem);
  if (p) el.outerHTML = renderCard(p);
}}

/* ---- Keyword bar ---- */
function renderKwBar() {{
  if (!ALL_KEYWORDS.length) return;
  const bar = document.getElementById('kw-bar');
  bar.style.display = '';
  const active = S.ui.kw_filter;
  bar.innerHTML = ['<button class="kw-chip' + (!active ? ' active' : '') + '" onclick="setKwFilter(null)">すべて</button>']
    .concat(ALL_KEYWORDS.map(k => {{
      const on = k === active;
      return `<button class="kw-chip ${{on ? 'active' : ''}}" data-kw="${{esc(k)}}" onclick="setKwFilter(this.dataset.kw)">${{esc(k)}}</button>`;
    }})).join('');
}}
window.setKwFilter = function(kw) {{ S.ui.kw_filter = kw; save(); renderKwBar(); renderGrid(); }};

/* ---- Actions ---- */
window.toggleAdopt = function(e, stem) {{
  if (e) e.stopPropagation();
  S.adopted[stem] = !S.adopted[stem];
  save(); updateCard(stem); updateAdoptCounter(); updateCollPanel();
}};
window.bulkAdoptVisible = function(adopt) {{
  const list = filterAndSort();
  if (!list.length) return;
  list.forEach(p => {{ if (adopt) S.adopted[p.stem] = true; else delete S.adopted[p.stem]; }});
  save(); renderGrid();
}};
function updateAdoptCounter() {{
  document.getElementById('adopt-count').textContent =
    PHOTOS.filter(p => !!S.adopted[p.stem]).length;
}}
window.setViewMode = function(mode) {{
  S.ui.view_mode = mode;
  ['list', 'group', 'adopted'].forEach(m => {{
    document.getElementById('vt-' + m).classList.toggle('active', m === mode);
  }});
  document.getElementById('group-threshold-wrap').style.display = mode === 'group' ? '' : 'none';
  save(); renderGrid();
}};

window.setRating = function(stem, val) {{
  if (val === 0 || S.ratings[stem] === val) delete S.ratings[stem];
  else S.ratings[stem] = val;
  save(); updateCard(stem);
  if ((S.ui.filter_level || 0) > 0) renderGrid();
}};

window.setNote = function(stem, val) {{
  if (val.trim()) S.notes[stem] = val; else delete S.notes[stem];
  save();
}};

/* ---- Tag modal ---- */
let _tagStem = null;
window.openTagModal = function(stem) {{
  _tagStem = stem;
  const cur = S.tags[stem] || [];
  document.getElementById('tag-modal-title').textContent = stem + ' — タグ編集';
  document.getElementById('tag-presets').innerHTML = PRESET_TAGS.map(t => {{
    const on = cur.includes(t);
    return `<button class="tag-preset-btn ${{on ? 'on' : ''}}" data-tag="${{esc(t)}}" onclick="togglePresetTag(this.dataset.tag)">${{on ? '✓ ' : ''}}${{esc(t)}}</button>`;
  }}).join('');
  document.getElementById('tag-custom-input').value = '';
  document.getElementById('tag-modal').classList.add('open');
}};
window.closeTagModal = function() {{
  document.getElementById('tag-modal').classList.remove('open'); _tagStem = null;
}};
window.closeTagModalOverlay = function(e) {{
  if (e.target === document.getElementById('tag-modal')) window.closeTagModal();
}};
window.openHelp = function() {{
  document.getElementById('help-modal').classList.add('open');
}};
window.closeHelp = function() {{
  document.getElementById('help-modal').classList.remove('open');
}};
window.closeHelpOutside = function(e) {{
  if (e.target === document.getElementById('help-modal')) closeHelp();
}};
window.togglePresetTag = function(tag) {{
  if (!_tagStem) return;
  const cur = S.tags[_tagStem] || [];
  const idx = cur.indexOf(tag);
  if (idx >= 0) cur.splice(idx, 1); else cur.push(tag);
  S.tags[_tagStem] = cur;
  save(); updateCard(_tagStem); window.openTagModal(_tagStem);
}};
window.addCustomTag = function() {{
  if (!_tagStem) return;
  const input = document.getElementById('tag-custom-input');
  const tag = input.value.trim();
  if (!tag) return;
  const cur = S.tags[_tagStem] || [];
  if (!cur.includes(tag)) {{ cur.push(tag); S.tags[_tagStem] = cur; save(); updateCard(_tagStem); }}
  input.value = ''; window.openTagModal(_tagStem);
}};
window.removeTag = function(stem, tag) {{
  const cur = S.tags[stem] || [];
  const idx = cur.indexOf(tag);
  if (idx >= 0) {{ cur.splice(idx, 1); save(); updateCard(stem); updateCollPanel(); }}
}};

/* ---- Collection panel ---- */
function updateCollPanel() {{
  const counts = {{}};
  Object.values(S.tags).forEach(tags => tags.forEach(t => {{ counts[t] = (counts[t] || 0) + 1; }}));
  const el = document.getElementById('coll-rows');
  if (!Object.keys(counts).length) {{
    el.innerHTML = '<div style="font-size:0.72rem;color:#c0b0a0">タグなし</div>'; return;
  }}
  el.innerHTML = Object.entries(counts).sort((a,b) => b[1]-a[1]).map(([t,n]) =>
    `<div class="coll-row" data-tag="${{esc(t)}}" onclick="filterByTag(this.dataset.tag)"><span class="coll-tag">${{esc(t)}}</span><span class="coll-n">${{n}}</span></div>`
  ).join('');
}}
window.filterByTag = function(tag) {{
  S.ui.search = tag; document.getElementById('search-box').value = tag; save(); renderGrid();
}};

/* ---- Grid Focus ---- */
let _focusedStem = null;
function setGridFocus(stem) {{
  if (_focusedStem) {{
    const prev = document.getElementById('card-' + _focusedStem);
    if (prev) prev.classList.remove('is-focused');
  }}
  _focusedStem = stem;
  if (!stem) return;
  const el = document.getElementById('card-' + stem);
  if (el) {{
    el.classList.add('is-focused');
    el.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});
  }}
}}
function moveGridFocus(offset) {{
  const list = filterAndSort();
  if (!list.length) return;
  const cur = _focusedStem ? list.findIndex(p => p.stem === _focusedStem) : -1;
  const next = cur < 0 ? 0 : Math.max(0, Math.min(list.length - 1, cur + offset));
  setGridFocus(list[next].stem);
}}

/* ---- Modal ---- */
let _modalStem = null;
function isModalOpen() {{
  return document.getElementById('modal').classList.contains('open') && !!_modalStem;
}}
function getModalList() {{
  return filterAndSort();
}}
function getModalIndex() {{
  if (!_modalStem) return -1;
  return getModalList().findIndex(p => p.stem === _modalStem);
}}
function updateModalNavButtons() {{
  const prevBtn = document.getElementById('modal-prev-btn');
  const nextBtn = document.getElementById('modal-next-btn');
  const index = getModalIndex();
  const hasList = index >= 0;
  const list = hasList ? getModalList() : [];
  prevBtn.disabled = !hasList || index === 0;
  nextBtn.disabled = !hasList || index === list.length - 1;
}}
window.showAdjacentModal = function(offset) {{
  const list = getModalList();
  const index = getModalIndex();
  if (index < 0) return;
  const next = list[index + offset];
  if (!next) return;
  window.openModal(next.url, next.stem);
}};
function updateModalRating() {{
  if (!_modalStem) return;
  const r = S.ratings[_modalStem] || 0;
  document.getElementById('modal-rating').textContent = r > 0 ? '★'.repeat(r) : '';
}}
window.openModal = function(url, stem) {{
  _modalStem = stem;
  document.getElementById('modal-img').src = url;
  document.getElementById('modal-name').textContent = stem;
  const btn = document.getElementById('modal-adopt-btn');
  const isAd = !!S.adopted[stem];
  btn.textContent = isAd ? '採用解除' : '採用する';
  btn.classList.toggle('on', isAd);
  document.getElementById('modal').classList.add('open');
  updateModalNavButtons();
  updateModalRating();
}};
window.closeModal = function(e) {{
  if (e && !e.target.classList.contains('modal') && !e.target.classList.contains('modal-close')) return;
  document.getElementById('modal').classList.remove('open');
  document.getElementById('modal-img').src = '';
  _modalStem = null;
}};
window.toggleAdoptModal = function() {{
  if (!_modalStem) return;
  window.toggleAdopt(null, _modalStem);
  const btn = document.getElementById('modal-adopt-btn');
  const isAd = !!S.adopted[_modalStem];
  btn.textContent = isAd ? '採用解除' : '採用する';
  btn.classList.toggle('on', isAd);
  updateModalNavButtons();
}};

/* ---- UI controls ---- */
window.onSearch = function(v) {{ S.ui.search = v; save(); renderGrid(); }};
window.onFilterCmpChange = function() {{ S.ui.filter_cmp = document.getElementById('filter-cmp').value; save(); renderGrid(); }};
window.setFilterLevel = function(n) {{
  S.ui.filter_level = (S.ui.filter_level === n) ? 0 : n;
  save(); renderFilterStars(); renderGrid();
}};
function renderFilterStars() {{
  const lvl = S.ui.filter_level || 0;
  const el = document.getElementById('filter-stars');
  if (!el) return;
  el.innerHTML = [1,2,3,4,5].map(i =>
    `<button class="filter-star-btn ${{i <= lvl ? 'on' : ''}}" onclick="setFilterLevel(${{i}})" title="${{i}}★">★</button>`
  ).join('');
}}
window.onSortChange = function() {{ S.ui.sort = document.getElementById('sort-select').value; save(); renderGrid(); }};
window.onSizeChange = function(v) {{
  S.ui.thumb_size = parseInt(v, 10);
  document.documentElement.style.setProperty('--thumb-size', v + 'px');
  save();
}};
window.onGroupThresholdChange = function(v) {{
  const sec = Math.max(1, parseInt(v, 10) || 1);
  S.ui.group_threshold = sec;
  regroupPhotos(sec);
  save(); renderGrid();
}};

/* ---- タグ集計ドロップダウン ---- */
window.toggleCollPanel = function(e) {{
  e.stopPropagation();
  document.getElementById('coll-panel').classList.toggle('open');
}};
document.addEventListener('click', function(e) {{
  if (!e.target.closest('.tag-summary-wrap')) {{
    const p = document.getElementById('coll-panel');
    if (p) p.classList.remove('open');
  }}
}});

/* ---- State import/export ---- */
window.exportState = function() {{
  S.ui.last_exported_at = new Date().toISOString();
  save();
  updateLastSavedLabel();
  const blob = new Blob([JSON.stringify(S, null, 2)], {{type:'application/json;charset=utf-8'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (TITLE_SLUG ? TITLE_SLUG + '-' : '') + 'saved-' + fmtDate(new Date()) + '.json';
  a.click();
  // blob URL は GC に任せる（revokeObjectURL は不要）
}};
window.triggerImport = function() {{
  const input = document.getElementById('state-file-input');
  input.value = '';
  input.click();
}};
window.importState = function(event) {{
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function() {{
    try {{
      const parsed = JSON.parse(reader.result);
      if (!mergeState(parsed)) {{
        alert('JSON の形式が正しくありません');
        return;
      }}
      save();
      updateLastSavedLabel();
      renderKwBar();
      renderGrid();
    }} catch (e) {{
      alert('JSON の読み込みに失敗しました');
    }}
  }};
  reader.onerror = function() {{
    alert('ファイルの読み込みに失敗しました');
  }};
  reader.readAsText(file);
}};

/* ---- Export ---- */
window.exportList = function() {{
  const adopted = PHOTOS.filter(p => S.adopted[p.stem]);
  if (!adopted.length) {{ alert('採用フラグが付いた写真がありません'); return; }}
  const lines = ['採用リスト', '', '採用数: ' + adopted.length + ' 枚', '---', ''];
  adopted.forEach(p => {{
    lines.push(p.stem);
    const r = effRating(p.stem);
    if (r > 0) lines.push('  レーティング: ' + '★'.repeat(r));
    const tags = (S.tags[p.stem] || []).join(', ');
    if (tags) lines.push('  タグ: ' + tags);
    const note = S.notes[p.stem] || '';
    if (note) lines.push('  メモ: ' + note);
    lines.push('');
  }});
  const blob = new Blob([lines.join('\\n')], {{type:'text/plain;charset=utf-8'}});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = (TITLE_SLUG ? TITLE_SLUG + '-' : '') + 'adopted_list.txt'; a.click();
}};

/* ---- Keyboard ---- */
document.addEventListener('keydown', e => {{
  const tag = document.activeElement ? document.activeElement.tagName : '';
  const isTextInput = tag === 'INPUT' || tag === 'TEXTAREA';
  if (isTextInput) return;

  if ((e.metaKey || e.ctrlKey) && (e.key === 'a' || e.key === 'A')) {{
    e.preventDefault();
    const list = filterAndSort();
    const allAdopted = list.length > 0 && list.every(p => S.adopted[p.stem]);
    window.bulkAdoptVisible(!allAdopted);
    return;
  }}

  if (e.key === 'Escape') {{
    if (document.getElementById('help-modal').classList.contains('open')) {{ closeHelp(); return; }}
    window.closeModal(); window.closeTagModal(); return;
  }}

  if (isModalOpen()) {{
    if (e.key === 'ArrowLeft')  {{ e.preventDefault(); window.showAdjacentModal(-1); return; }}
    if (e.key === 'ArrowRight') {{ e.preventDefault(); window.showAdjacentModal(1);  return; }}
    if (e.key === ' ' || e.code === 'Space') {{
      e.preventDefault();
      const p = filterAndSort().find(x => x.stem === _modalStem);
      window.closeModal();
      // フォーカスは維持したまま次の写真へ進む準備（モーダルを閉じるだけ）
      return;
    }}
    if (e.key === 'a' || e.key === 'A') {{ e.preventDefault(); window.toggleAdoptModal(); return; }}
    if (['1','2','3','4','5'].includes(e.key)) {{
      e.preventDefault();
      const v = parseInt(e.key, 10);
      window.setRating(_modalStem, S.ratings[_modalStem] === v ? 0 : v);
      updateModalRating();
      return;
    }}
  }} else {{
    if (e.key === 'ArrowLeft')  {{ e.preventDefault(); moveGridFocus(-1); return; }}
    if (e.key === 'ArrowRight') {{ e.preventDefault(); moveGridFocus(1);  return; }}
    if ((e.key === ' ' || e.code === 'Space') && _focusedStem) {{
      e.preventDefault();
      const p = filterAndSort().find(x => x.stem === _focusedStem);
      if (p) window.openModal(p.url, p.stem);
      return;
    }}
    if ((e.key === 'a' || e.key === 'A') && _focusedStem) {{
      e.preventDefault(); window.toggleAdopt(null, _focusedStem); return;
    }}
    if (e.key === 'Enter' && _focusedStem) {{
      e.preventDefault();
      const p = filterAndSort().find(x => x.stem === _focusedStem);
      if (p) window.openModal(p.url, p.stem);
      return;
    }}
    if (['1','2','3','4','5'].includes(e.key) && _focusedStem) {{
      e.preventDefault();
      const v = parseInt(e.key, 10);
      window.setRating(_focusedStem, S.ratings[_focusedStem] === v ? 0 : v);
      return;
    }}
  }}
}});

/* ---- 保存警告バナー ---- */
window.dismissWarning = function() {{
  document.getElementById('save-warning').style.display = 'none';
  try {{ sessionStorage.setItem('save-warning-dismissed', '1'); }} catch(e) {{}}
}};

/* ---- Init ---- */
load();
document.getElementById('filter-cmp').value = S.ui.filter_cmp || 'gte';
renderFilterStars();
document.getElementById('sort-select').value = S.ui.sort       || 'dt_asc';
document.getElementById('size-slider').value = S.ui.thumb_size || 260;
document.getElementById('search-box').value  = S.ui.search     || '';
document.documentElement.style.setProperty('--thumb-size', (S.ui.thumb_size || 260) + 'px');
const _initVm = S.ui.view_mode || 'list';
['list', 'group', 'adopted'].forEach(m => {{
  document.getElementById('vt-' + m).classList.toggle('active', m === _initVm);
}});
const _initThreshold = S.ui.group_threshold || GROUP_THRESHOLD_DEFAULT;
document.getElementById('group-threshold-input').value = _initThreshold;
document.getElementById('group-threshold-wrap').style.display = _initVm === 'group' ? '' : 'none';
regroupPhotos(_initThreshold);
updateLastSavedLabel();
try {{
  if (sessionStorage.getItem('save-warning-dismissed')) {{
    document.getElementById('save-warning').style.display = 'none';
  }}
}} catch(e) {{}}
renderKwBar();
renderGrid();
</script>
</body>
</html>'''


def main() -> None:
    parser = argparse.ArgumentParser(
        description='select-share: LightroomエクスポートフォルダからクライアントHTMLを生成',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('jpeg_dir', help='JPEGが格納されているフォルダ')
    parser.add_argument('--output',      default='./delivery', help='出力フォルダ')
    parser.add_argument('--title',       default='',           help='ギャラリータイトル')
    parser.add_argument('--min-rating',  type=int, default=1,  help='最低レーティング（0=すべて）')
    parser.add_argument('--no-copy',     action='store_true',  help='JPEGをコピーしない（参照のみ）')
    parser.add_argument('--move',        action='store_true',  help='コピーではなく移動する（元ファイルが削除される）')
    parser.add_argument('--no-zip',      action='store_true',  help='zipを作成しない')
    parser.add_argument('--group-threshold', type=int, default=3, metavar='SEC',
                        help='グループ分けの撮影時刻間隔（秒）デフォルト: 3')
    parser.add_argument('--theme',       default='default', choices=list(THEMES), help='テーマプリセットを選択する')
    parser.add_argument('--extra-css',   default='', metavar='FILE', help='追加CSSファイルのパス')
    parser.add_argument('--key-color',   default='', metavar='COLOR', help='テーマ生成に使うキーカラー（例: #9d342b）')
    parser.add_argument('--credit',      default='', help='著作権者名（例: 村山写真事務所）')
    args = parser.parse_args()

    jpeg_dir = Path(args.jpeg_dir)
    if not jpeg_dir.is_dir():
        print(f'ERROR: フォルダが見つかりません: {jpeg_dir}', file=sys.stderr)
        sys.exit(1)

    # exiftool チェック
    try:
        subprocess.run(['exiftool', '-ver'], check=True, capture_output=True)
    except Exception:
        print('ERROR: exiftool がインストールされていません。\n  brew install exiftool', file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    title = args.title or jpeg_dir.name

    print(f'[select-share] メタデータ読み取り中: {jpeg_dir}')
    exif_data = run_exiftool(jpeg_dir)
    print(f'  → {len(exif_data)} ファイル検出')

    photos = build_photo_db(exif_data, min_rating=args.min_rating)
    print(f'  → レーティング {args.min_rating}★ 以上: {len(photos)} 枚')
    assign_groups(photos, threshold_seconds=args.group_threshold)

    if not photos:
        print('ERROR: 対象写真がありません。--min-rating を下げてください。', file=sys.stderr)
        sys.exit(1)

    # 画像コピー
    photos_dir = output_dir / 'photos'
    if not args.no_copy:
        verb = '移動' if args.move else 'コピー'
        print(f'[select-share] JPEG{verb}中 → {photos_dir}')
        copy_images(photos, photos_dir, move=args.move)
    # --no-copy でも photos/ サブフォルダが存在すれば相対パスで参照（配信用）
    photos_prefix = 'photos'

    extra_css_parts = []
    if args.key_color:
        try:
            extra_css_parts.append(derive_theme(args.key_color))
        except ValueError as e:
            print(f'ERROR: --key-color が不正です: {e}', file=sys.stderr)
            sys.exit(1)
    elif THEMES.get(args.theme):
        extra_css_parts.append(THEMES[args.theme])
    if args.extra_css:
        extra_css_path = Path(args.extra_css)
        if not extra_css_path.is_file():
            print(f'ERROR: CSSファイルが見つかりません: {extra_css_path}', file=sys.stderr)
            sys.exit(1)
        extra_css_parts.append(extra_css_path.read_text(encoding='utf-8'))
    extra_css = '\n'.join(extra_css_parts)

    # HTML生成
    print('[select-share] HTML生成中…')
    html_str = generate_html(photos, title, photos_prefix, extra_css=extra_css, group_threshold=args.group_threshold, credit=args.credit)
    html_path = output_dir / 'index.html'
    html_path.write_text(html_str, encoding='utf-8')
    print(f'  → {html_path}')

    # readme.txt 生成
    readme_path = output_dir / 'readme.txt'
    readme_path.write_text(generate_readme(title), encoding='utf-8')
    print(f'  → {readme_path}')

    # zip
    if not args.no_zip:
        zip_path = output_dir.parent / f'{output_dir.name}.zip'
        print(f'[select-share] zip作成中: {zip_path}')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for f in output_dir.rglob('*'):
                if f.is_file():
                    zf.write(f, f.relative_to(output_dir.parent))
        print(f'  → {zip_path} ({zip_path.stat().st_size // 1024 // 1024} MB)')

    print(f'\n完了。ブラウザで確認:')
    print(f'  open "{html_path}"')
    if not args.no_zip:
        print(f'\nクライアントへ送付:')
        print(f'  {zip_path}')


if __name__ == '__main__':
    main()
