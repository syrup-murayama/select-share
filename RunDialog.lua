-- select-share-runner.lrplugin/RunDialog.lua

local LrBinding         = import 'LrBinding'
local LrColor           = import 'LrColor'
local LrDialogs         = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrPrefs           = import 'LrPrefs'
local LrTasks           = import 'LrTasks'
local LrView            = import 'LrView'

local f = LrView.osFactory()

-- Homebrew installs exiftool to one of these paths depending on arch
local EXIFTOOL_SEARCH_PATHS = {
  '/opt/homebrew/bin/exiftool',   -- Apple Silicon
  '/usr/local/bin/exiftool',      -- Intel
}

local function findExiftool()
  for _, path in ipairs(EXIFTOOL_SEARCH_PATHS) do
    local fh = io.open(path, 'r')
    if fh then fh:close(); return path end
  end
  return nil
end

-- Shell-quote a string for POSIX sh (single-quote style)
local function q(s)
  if not s or s == '' then return "''" end
  return "'" .. s:gsub("'", "'\\''") .. "'"
end

local function getBuildPyPath()
  return _PLUGIN.path .. '/select-share/build.py'
end

local function checkBuildPy()
  local file = io.open(getBuildPyPath(), 'r')
  if file then file:close(); return true end
  return false
end

local function loadPrefs(props)
  local prefs = LrPrefs.prefsForPlugin()
  props.jpeg_dir        = prefs.jpeg_dir        or ''
  props.output          = prefs.output          or './delivery'
  props.title           = prefs.title           or ''
  props.credit_name     = prefs.credit_name     or ''
  props.min_rating      = prefs.min_rating      or 1
  props.theme           = prefs.theme           or 'default'
  props.group_threshold = prefs.group_threshold or 3
  props.file_handling   = prefs.file_handling   or 'copy'
  props.no_zip          = prefs.no_zip          or false
  props.extra_css       = prefs.extra_css       or ''
  props.key_color       = prefs.key_color       or ''
end

local function savePrefs(props)
  local prefs = LrPrefs.prefsForPlugin()
  prefs.jpeg_dir        = props.jpeg_dir
  prefs.output          = props.output
  prefs.title           = props.title
  prefs.credit_name     = props.credit_name
  prefs.min_rating      = props.min_rating
  prefs.theme           = props.theme
  prefs.group_threshold = props.group_threshold
  prefs.file_handling   = props.file_handling
  prefs.no_zip          = props.no_zip
  prefs.extra_css       = props.extra_css
  prefs.key_color       = props.key_color
end

local function buildCommand(props, exiftoolPath)
  -- Prepend the directory containing exiftool to PATH so build.py can find it
  local exiftoolDir = exiftoolPath:match('(.+)/[^/]+$')
  local cmd = 'PATH=' .. exiftoolDir .. ':"$PATH" /usr/bin/python3 '
              .. q(getBuildPyPath()) .. ' ' .. q(props.jpeg_dir)

  if props.output and props.output ~= '' then
    cmd = cmd .. ' --output ' .. q(props.output)
  end
  if props.title and props.title ~= '' then
    cmd = cmd .. ' --title ' .. q(props.title)
  end
  if props.min_rating then
    cmd = cmd .. ' --min-rating ' .. tostring(props.min_rating)
  end
  if props.theme and props.theme ~= 'default' then
    cmd = cmd .. ' --theme ' .. props.theme
  end
  if props.group_threshold and tonumber(props.group_threshold) then
    cmd = cmd .. ' --group-threshold ' .. tostring(props.group_threshold)
  end
  if props.file_handling == 'move' then cmd = cmd .. ' --move' end
  if props.no_zip then cmd = cmd .. ' --no-zip' end
  if props.extra_css and props.extra_css ~= '' then
    cmd = cmd .. ' --extra-css ' .. q(props.extra_css)
  end
  if props.key_color and props.key_color ~= '' then
    cmd = cmd .. ' --key-color ' .. q(props.key_color)
  end
  if props.credit_name and props.credit_name ~= '' then
    cmd = cmd .. ' --credit ' .. q(props.credit_name)
  end

  return cmd .. ' 2>&1'
end

LrFunctionContext.callWithContext('SelectShareRunner', function(context)
  -- Observable property table: UI updates automatically when values change
  local props = LrBinding.makePropertyTable(context)
  loadPrefs(props)

  local FIELD_W = 280

  local contents = f:column {
    spacing = f:control_spacing(),

    f:group_box {
      title = '対象フォルダ',
      f:column {
        spacing = f:control_spacing(),
        f:row {
          f:push_button {
            title = '入力フォルダを選択...',
            action = function()
              local r = LrDialogs.runOpenPanel {
                title                   = 'JPEGフォルダを選択',
                canChooseFiles          = false,
                canChooseDirectories    = true,
                canCreateDirectories    = true,
                allowsMultipleSelection = false,
              }
              if r then props.jpeg_dir = r[1] end
            end,
          },
          f:static_text {
            title = LrView.bind {
              key            = 'jpeg_dir',
              bind_to_object = props,
              transform      = function(v) return (v and v ~= '') and v or '（未選択）' end,
            },
            text_color = LrColor(0.4, 0.4, 0.4),
            width      = FIELD_W,
            truncation = 'middle',
            tooltip    = LrView.bind { key = 'jpeg_dir', bind_to_object = props },
          },
        },
        f:row {
          f:push_button {
            title = '書き出し先を選択...',
            action = function()
              local r = LrDialogs.runOpenPanel {
                title                   = '出力フォルダを選択',
                canChooseFiles          = false,
                canChooseDirectories    = true,
                canCreateDirectories    = true,
                allowsMultipleSelection = false,
              }
              if r then props.output = r[1] end
            end,
          },
          f:static_text {
            title = LrView.bind {
              key            = 'output',
              bind_to_object = props,
              transform      = function(v) return (v and v ~= '') and v or '（未選択）' end,
            },
            width      = FIELD_W,
            truncation = 'middle',
            tooltip    = LrView.bind { key = 'output', bind_to_object = props },
          },
        },
      },
    },

    f:group_box {
      title = 'オプション',
      f:column {
        spacing = f:control_spacing(),
        f:row {
          f:static_text { title = 'タイトル:', width = 110 },
          f:edit_field {
            value = LrView.bind { key = 'title', bind_to_object = props },
            width = FIELD_W + 60,
          },
        },
        f:row {
          f:static_text { title = 'クレジット:', width = 110 },
          f:edit_field {
            value = LrView.bind { key = 'credit_name', bind_to_object = props },
            width = FIELD_W + 60,
            placeholder_string = '例: 村山写真事務所',
          },
        },
        f:row {
          f:static_text { title = '最低レーティング:', width = 110 },
          f:popup_menu {
            value = LrView.bind { key = 'min_rating', bind_to_object = props },
            items = {
              { title = '0 (すべて)',   value = 0 },
              { title = '1 ★',         value = 1 },
              { title = '2 ★★',       value = 2 },
              { title = '3 ★★★',     value = 3 },
              { title = '4 ★★★★',   value = 4 },
              { title = '5 ★★★★★', value = 5 },
            },
          },
          f:spacer { width = 20 },
          f:static_text { title = 'テーマ:', width = 50 },
          f:popup_menu {
            value = LrView.bind { key = 'theme', bind_to_object = props },
            items = {
              { title = 'Default', value = 'default' },
              { title = 'Natural', value = 'natural' },
              { title = 'Navy',    value = 'navy' },
              { title = 'Gold',    value = 'gold' },
            },
          },
          f:spacer { width = 20 },
          f:static_text { title = 'グループ閾値(秒):', width = 100 },
          f:edit_field {
            value = LrView.bind { key = 'group_threshold', bind_to_object = props },
            width = 50,
          },
        },
        f:row {
          f:static_text { title = 'ファイル処理:', width = 110 },
          f:popup_menu {
            value = LrView.bind { key = 'file_handling', bind_to_object = props },
            items = {
              { title = 'コピー（デフォルト）',     value = 'copy' },
              { title = '移動（元ファイルを削除）', value = 'move' },
            },
          },
          f:spacer { width = 20 },
          f:checkbox {
            title = 'ZIPを作成しない',
            value = LrView.bind { key = 'no_zip', bind_to_object = props },
          },
        },
        f:row {
          f:push_button {
            title = '追加CSSを選択...',
            action = function()
              local r = LrDialogs.runOpenPanel {
                title = 'CSSファイルを選択',
                canChooseFiles = true,
                canChooseDirectories = false,
                allowsMultipleSelection = false,
              }
              if r then props.extra_css = r[1] end
            end,
          },
          f:static_text {
            title = LrView.bind {
              key            = 'extra_css',
              bind_to_object = props,
              transform      = function(v) return (v and v ~= '') and v or '（なし）' end,
            },
            text_color = LrColor(0.4, 0.4, 0.4),
            width      = FIELD_W,
            truncation = 'middle',
            tooltip    = LrView.bind { key = 'extra_css', bind_to_object = props },
          },
        },
        f:row {
          f:static_text { title = 'キーカラー:', width = 110 },
          f:edit_field {
            value = LrView.bind { key = 'key_color', bind_to_object = props },
            width = 120,
            placeholder_string = '#9d342b',
          },
        },
      },
    },
  }

  local result = LrDialogs.presentModalDialog {
    title          = 'Select Share ビルダー',
    contents       = contents,
    bind_to_object = props,
    actionVerb     = '実行',
    cancelVerb     = 'キャンセル',
    resizable      = true,
  }

  if result ~= 'ok' then return end

  if not props.jpeg_dir or props.jpeg_dir == '' then
    LrDialogs.message('入力エラー', 'JPEGフォルダを選択してください。', 'critical')
    return
  end

  if not checkBuildPy() then
    LrDialogs.message('エラー', 'build.py が見つかりません:\n' .. getBuildPyPath(), 'critical')
    return
  end

  local exiftoolPath = findExiftool()
  if not exiftoolPath then
    local choice = LrDialogs.confirm(
      'exiftool が見つかりません',
      'このプラグインの動作には exiftool が必要です。\n\n' ..
      '公式サイトから macOS 用インストーラー（.pkg）をダウンロードして\n' ..
      'インストールしてください。インストール後、再実行してください。',
      'ダウンロードページを開く',
      '閉じる'
    )
    if choice == 'ok' then
      os.execute('open "https://exiftool.org/"')
    end
    return
  end

  savePrefs(props)

  local cmd = buildCommand(props, exiftoolPath)

  LrTasks.startAsyncTask(function()
    local fh = io.popen(cmd)
    local output = fh:read('*a')
    local ok = fh:close()
    LrDialogs.message(
      ok and 'ビルド完了' or 'ビルド失敗',
      output,
      ok and 'info' or 'critical'
    )
  end)
end)
