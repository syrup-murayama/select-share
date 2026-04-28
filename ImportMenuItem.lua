--[[
  ImportMenuItem.lua — セレクト結果を読み込む

  Flow:
    1. File picker → select adopted_list.txt
    2. Parse adopted_list.txt  → list of {stem, rating, memo}
    3. Build candidate photo index from LrC catalog
       (uses actual file paths stored in catalog — no RAW dir argument needed)
    4. Substring-match each adopted stem against catalog filename stems
    5. Confirmation dialog showing match counts
    6. Apply metadata via catalog:withWriteAccessDo:
         - pickStatus = 'pick'
         - rating     = N
         - caption    = memo (when present)
    7. Create new collection "採用 YYYY-MM-DD" and add matched photos
]]

local LrApplication   = import 'LrApplication'
local LrDialogs       = import 'LrDialogs'
local LrFunctionContext = import 'LrFunctionContext'
local LrPathUtils     = import 'LrPathUtils'
local LrTasks         = import 'LrTasks'

-- ---------------------------------------------------------------------------
-- Utilities
-- ---------------------------------------------------------------------------

local function trim(s)
  return s:match('^%s*(.-)%s*$')
end

local function startsWith(s, prefix)
  return s:sub(1, #prefix) == prefix
end

-- ---------------------------------------------------------------------------
-- Parse adopted_list.txt
-- Returns: list of {stem=string, rating=int, memo=string}
-- ---------------------------------------------------------------------------

local SKIP_PREFIXES = { '採用リスト', '採用数', '---' }
local RATING_PREFIX = 'レーティング:'
local MEMO_PREFIX   = 'メモ:'
local STAR_MAP = {
  ['★']     = 1,
  ['★★']    = 2,
  ['★★★']   = 3,
  ['★★★★']  = 4,
  ['★★★★★'] = 5,
}

local function parseAdoptedList(path)
  local file, err = io.open(path, 'r')
  if not file then
    return nil, 'ファイルを開けません: ' .. (err or path)
  end

  local entries = {}
  local current = nil

  for line in file:lines() do
    local stripped = trim(line)

    if stripped ~= '' then
      -- Check skip prefixes
      local skip = false
      for _, prefix in ipairs(SKIP_PREFIXES) do
        if startsWith(stripped, prefix) then skip = true; break end
      end

      if not skip then
        if startsWith(stripped, RATING_PREFIX) then
          if current then
            local stars = trim(stripped:sub(#RATING_PREFIX + 1))
            current.rating = STAR_MAP[stars] or 0
          end

        elseif startsWith(stripped, MEMO_PREFIX) then
          if current then
            current.memo = trim(stripped:sub(#MEMO_PREFIX + 1))
          end

        elseif not line:match('^%s') then
          -- Non-indented line = new stem entry
          if current then table.insert(entries, current) end
          current = { stem = stripped, rating = 0, memo = '' }
        end
      end
    end
  end

  if current then table.insert(entries, current) end
  file:close()

  return entries
end

-- ---------------------------------------------------------------------------
-- Extract a date hint from the first stem
-- e.g. "20260408__A6A0304_..." → "2026-04-08"
-- ---------------------------------------------------------------------------

local function extractDateHint(entries)
  if #entries == 0 then return nil end
  local y, m, d = entries[1].stem:match('^(%d%d%d%d)(%d%d)(%d%d)')
  if y then return y .. '-' .. m .. '-' .. d end
  return nil
end

-- ---------------------------------------------------------------------------
-- Build catalog photo index
-- Returns: list of {stem=string, photo=LrPhoto}
-- Optionally filtered to photos whose path contains dateHint.
-- ---------------------------------------------------------------------------

local function buildPhotoIndex(catalog, dateHint)
  local allPhotos = catalog:getAllPhotos()
  local index = {}

  for _, photo in ipairs(allPhotos) do
    local path = photo:getRawMetadata('path') or ''

    -- Filter by date hint to avoid scanning the entire catalog needlessly
    if not dateHint or path:find(dateHint, 1, true) then
      local filename = LrPathUtils.leafName(path)
      local stem     = LrPathUtils.removeExtension(filename)
      if stem and stem ~= '' then
        -- Keep first match per stem (duplicates are edge cases)
        if not index[stem] then
          index[stem] = photo
        end
      end
    end
  end

  return index
end

-- ---------------------------------------------------------------------------
-- Match adopted entries against catalog photo index
-- Strategy: catalog filename stem is a substring of the adopted stem
-- e.g.  catalog stem "_A6A0304"  found inside  "20260408__A6A0304_keywords_sample"
-- ---------------------------------------------------------------------------

local function matchEntries(entries, photoIndex)
  local matched   = {}  -- list of {entry, photo}
  local unmatched = {}  -- list of entry

  for _, entry in ipairs(entries) do
    local found = nil
    for catalogStem, photo in pairs(photoIndex) do
      if entry.stem:find(catalogStem, 1, true) then
        found = photo
        break
      end
    end

    if found then
      table.insert(matched,   { entry = entry, photo = found })
    else
      table.insert(unmatched, entry)
    end
  end

  return matched, unmatched
end

-- ---------------------------------------------------------------------------
-- Apply metadata and create collection
-- ---------------------------------------------------------------------------

local function applyMetadata(catalog, matched, collectionName)
  catalog:withWriteAccessDo('セレクト結果を適用', function()
    -- Create (or reuse) collection
    local collection = catalog:createCollection(collectionName, nil, true)

    local photosForCollection = {}

    for _, m in ipairs(matched) do
      local photo = m.photo
      local entry = m.entry

      -- Pick flag (採用)
      photo:setRawMetadata('pickStatus', 'pick')

      -- Client rating
      if entry.rating > 0 then
        photo:setRawMetadata('rating', entry.rating)
      end

      -- Memo → caption
      if entry.memo and entry.memo ~= '' then
        photo:setRawMetadata('caption', entry.memo)
      end

      table.insert(photosForCollection, photo)
    end

    -- Add matched photos to the new collection
    collection:addPhotos(photosForCollection)
  end)
end

-- ---------------------------------------------------------------------------
-- Build summary message for confirmation dialog
-- ---------------------------------------------------------------------------

local function buildSummaryMessage(matched, unmatched, collectionName)
  local lines = {
    string.format('%d 枚がカタログ内で見つかりました。', #matched),
  }

  if #unmatched > 0 then
    table.insert(lines, string.format('%d 枚は見つかりませんでした:', #unmatched))
    -- Show up to 5 unmatched stems
    local limit = math.min(#unmatched, 5)
    for i = 1, limit do
      table.insert(lines, '  • ' .. unmatched[i].stem)
    end
    if #unmatched > limit then
      table.insert(lines, string.format('  … 他 %d 件', #unmatched - limit))
    end
    table.insert(lines, '')
  end

  table.insert(lines, string.format('コレクション「%s」を作成し、採用フラグ・レーティングを適用します。', collectionName))

  return table.concat(lines, '\n')
end

-- ---------------------------------------------------------------------------
-- Main entry point
-- ---------------------------------------------------------------------------

LrTasks.startAsyncTask(function()
  LrFunctionContext.callWithContext('selectShareImport', function(context)
    LrDialogs.attachErrorDialogToFunctionContext(context)

    local catalog = LrApplication.activeCatalog()

    -- 1. File picker
    local paths = LrDialogs.runOpenPanel({
      title                  = 'adopted_list.txt を選択',
      canChooseFiles         = true,
      canChooseDirectories   = false,
      allowsMultipleSelection = false,
    })

    if not paths or #paths == 0 then return end  -- cancelled
    local adoptedListPath = paths[1]

    -- 2. Parse
    local entries, parseErr = parseAdoptedList(adoptedListPath)
    if not entries then
      LrDialogs.message('エラー', parseErr, 'critical')
      return
    end
    if #entries == 0 then
      LrDialogs.message('エラー', 'エントリが見つかりませんでした。\nファイル形式を確認してください。', 'critical')
      return
    end

    -- 3. Build catalog photo index (filtered by date hint)
    local dateHint = extractDateHint(entries)
    local photoIndex = buildPhotoIndex(catalog, dateHint)

    if next(photoIndex) == nil then
      LrDialogs.message(
        '写真が見つかりません',
        string.format(
          'カタログ内に %s の写真が見つかりませんでした。\n対象フォルダがカタログに読み込まれているか確認してください。',
          dateHint or '対象日付'
        ),
        'critical'
      )
      return
    end

    -- 4. Match
    local matched, unmatched = matchEntries(entries, photoIndex)

    if #matched == 0 then
      LrDialogs.message(
        '一致する写真がありません',
        '採用リストの写真がカタログ内で見つかりませんでした。\nファイル名の命名規則を確認してください。',
        'critical'
      )
      return
    end

    -- 5. Confirmation dialog
    local collectionName = '採用 ' .. (dateHint or 'unknown')
    local summary        = buildSummaryMessage(matched, unmatched, collectionName)
    local choice         = LrDialogs.confirm('セレクト結果を読み込む', summary, '適用する', 'キャンセル')

    if choice ~= 'ok' then return end

    -- 6. Apply
    applyMetadata(catalog, matched, collectionName)

    -- 7. Done
    LrDialogs.message(
      '完了',
      string.format(
        '%d 枚のメタデータを更新し、コレクション「%s」を作成しました。',
        #matched, collectionName
      )
    )
  end)
end)
