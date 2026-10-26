-- The handoff launch buffer (handoff.py opens it with +luafile): markdown with YAML frontmatter. The fields,
-- the detected skills and the help (YAML # comments) sit between two --- lines drawn as bars; the prompt is
-- the markdown below them. What it needs comes from
-- <stem>.launch.values.json, which handoff.py writes fresh on every press: handoff.toml, the presets, and
-- each skill's description and path. Everything here is buffer-local; ~/.config/nvim is not touched.
local buf = vim.api.nvim_get_current_buf()
local name = vim.api.nvim_buf_get_name(buf)
local values = vim.json.decode(table.concat(vim.fn.readfile((name:gsub("%.launch%.md$", ".launch.values.json"))), "\n"))
local ns = vim.api.nvim_create_namespace("handoff")
local draw = vim.api.nvim_create_namespace("handoff-draw")
local ERROR, WARN = vim.diagnostic.severity.ERROR, vim.diagnostic.severity.WARN
local CHECKED = { "model", "effort", "preset", "others", "old" } -- a bad one blocks :wq; skills only warn
local docs, presets, defaults, models = values.skill_docs, values.presets, values.defaults, values.models or {}
local known, shown = {}, {}
for _, skill in ipairs(values.skills) do known[skill] = true end
for skill in pairs(docs) do known[skill] = true end
for _, key in ipairs(values.config.show) do shown[key] = true end

-- The file is .launch.md, so nvim opens it as markdown: # in the prompt is a heading, and the frontmatter
-- gets YAML highlighting. What markdown would draw wrong is drawn over in paint().

-- Words YAML 1.1 reads as booleans or null, as in handoff.py.
local YAML_WORDS = { y = 1, yes = 1, n = 1, no = 1, ["true"] = 1, ["false"] = 1, on = 1, off = 1, null = 1, ["~"] = 1 }
local function needs_quotes(v)
  return v == "" or YAML_WORDS[v:lower()] or v:match("^[%-%+%.%d%[%]{}#&*!|>'\"%%@`,?:]") or v:find(": ", 1, true)
    or v:find(" #", 1, true)
end
-- A field line YAML reads back as the same string; handoff.py's row() and yaml_value(), same rule.
local function quoted(v) return needs_quotes(v) and ('"' .. v:gsub('[\\"]', "\\%0") .. '"') or v end
local function row(key, value) return ("%-8s%s"):format(key .. ":", quoted(value)) end
-- A YAML scalar as parse_buffer's BaseLoader reads it, always text: a quoted one unquoted, a plain one up
-- to its # comment, so a bare off is "off".
local function scalar(raw)
  raw = vim.trim(raw)
  local dq, sq = raw:match('^"(.*)"$'), raw:match("^'(.*)'$")
  if dq then return (dq:gsub("\\(.)", "%1")) end
  if sq then return (sq:gsub("''", "'")) end
  return vim.trim((raw:gsub("%s#.*$", "")))
end

-- The buffer by line (0-based), read the way handoff.py's parse_buffer reads it: the frontmatter between a
-- first-line --- and the next --- (its fields; its # comments, the detected skills line among them), then
-- the prompt, all of what follows. No frontmatter: all of it is the prompt.
local function shape()
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local s, prompt = { bars = {}, fields = {}, comments = {}, prompt_lines = {} }, {}
  if lines[1] == "---" then
    for i = 2, #lines do
      if lines[i] == "---" then s.bars = { 0, i - 1 }; break end
    end
  end
  for i, line in ipairs(lines) do
    local lnum = i - 1
    if not s.bars[2] or lnum > s.bars[2] then
      table.insert(prompt, line)
      table.insert(s.prompt_lines, { lnum = lnum, text = line })
    elseif lnum > 0 and lnum < s.bars[2] and line:match("^# detected skills:") then
      s.detected = s.detected or lnum
    elseif lnum > 0 and lnum < s.bars[2] and line:match("^%s*#") then
      table.insert(s.comments, lnum)
    elseif lnum > 0 and lnum < s.bars[2] then
      local key, rest = line:match("^(%w+):(.*)$")
      if key then
        s.fields[key] = { lnum = lnum, col = #key + 1 + #rest:match("^%s*"), value = scalar(rest) }
        s.last_field = lnum
      end
    end
  end
  s.prompt = vim.trim(table.concat(prompt, "\n"))
  return s
end

-- Each /name in text: (start column, name, known?). After a space, bracket or quote, and not a path (/mnt/c).
local function names(text)
  local out = {}
  for start, run, finish in text:gmatch("()/([%w_][%w_%.:%-]*)()") do
    local before = start > 1 and text:sub(start - 1, start - 1) or " "
    if before:match("[%s(\"']") and text:sub(finish, finish) ~= "/" then
      local skill = run:gsub("[%.:%-]+$", "")
      table.insert(out, { col = start - 1, name = skill, known = known[skill] })
    end
  end
  return out
end

-- The prompt's known /names, then skills: (the preset's), as handoff.py's detected() works them out.
local function detected(s)
  local out, seen = {}, {}
  local function add(skill) if not seen[skill] then seen[skill] = true; table.insert(out, skill) end end
  for _, n in ipairs(names(s.prompt)) do if n.known then add(n.name) end end
  for skill in (s.fields.skills and s.fields.skills.value or ""):gmatch("[^,%s]+") do add(skill) end
  return out
end

-- Writes made for you join the undo step of the edit that caused them, and happen only when needed, so
-- u takes back your edit and its effects together.
local function set_lines(first, last, new)
  if vim.deep_equal(vim.api.nvim_buf_get_lines(buf, first, last, false), new) then return end
  pcall(vim.cmd, "silent! undojoin")
  vim.api.nvim_buf_set_lines(buf, first, last, false, new)
end

-- What effort: may be with this model, as handoff.py's efforts_for(): [[effort, description]], {} = unchecked.
local function levels(model)
  if models[model] then return models[model].efforts end
  return (model or ""):match("^claude%-") and vim.tbl_map(function(e) return { e, "" } end, values.effort or {}) or {}
end

local function notice(text) vim.schedule(function() vim.api.nvim_echo({ { "handoff: " .. text, "WarningMsg" } }, true, {}) end) end

-- A preset fills in the fields it sets, and gives back their defaults to the ones the last preset set. Its
-- first prompt goes in only over an empty prompt or the last preset's: what you wrote stays.
local function apply(from, to)
  local old, new = presets[from] or { fields = {}, prompt = "" }, presets[to]
  for _, key in ipairs(values.order) do
    if old.fields[key] ~= nil or new.fields[key] ~= nil then
      local s, value = shape(), new.fields[key] or defaults[key] or ""
      if s.fields[key] then
        set_lines(s.fields[key].lnum, s.fields[key].lnum + 1, { row(key, value) })
      elseif s.bars[2] then
        local after = (s.last_field or 0) + 1
        set_lines(after, after, { row(key, value) })
      end
    end
  end
  local s = shape()
  if s.prompt == "" or s.prompt == vim.trim(old.prompt) then
    set_lines(s.bars[2] + 1, -1, { "", unpack(new.prompt ~= "" and vim.split(vim.trim(new.prompt), "\n") or { "" }) })
  elseif new.prompt ~= "" then
    notice(("kept your prompt; %s's own is in handoff-presets.md"):format(to))
  end
end

local function problems(s)
  if not s.bars[2] then
    return { { lnum = 0, col = 0, severity = ERROR,
      message = "the fields need their frontmatter: a --- first line, the fields, a --- line, then the prompt" } }
  end
  local out = {}
  local model = s.fields.model and s.fields.model.value or defaults.model
  local codex_warning = values.codex_warning or ""
  for _, key in ipairs(CHECKED) do
    local f = s.fields[key]
    local value = f and f.value or defaults[key]
    local allowed = key == "effort" and vim.tbl_map(function(l) return l[1] end, levels(model)) or values[key] or {}
    -- claude takes any full model name; a gpt- one is codex's to know, unless its catalog could not be read
    local known = key == "model" and value and (models[value] or value:match("^claude%-")
      or value:match("^gpt%-") and codex_warning ~= "")
    if #allowed > 0 and not vim.tbl_contains(allowed, value) and not known then
      table.insert(out, { lnum = f and f.lnum or s.bars[1], col = f and f.col or 0, severity = ERROR,
        message = ("%s: %s is not one of %s%s"):format(key, value ~= "" and value or "(empty)", table.concat(allowed, " "),
          key == "effort" and (" (what %s supports)"):format(model) or "") })
    end
  end
  local m = models[model]
  if codex_warning ~= "" and s.fields.model and (m and m.provider == "codex" or (model or ""):match("^gpt%-")) then
    table.insert(out, { lnum = s.fields.model.lnum, col = s.fields.model.col, severity = WARN, message = codex_warning })
  end
  local skills = s.fields.skills
  for skill in (skills and skills.value or ""):gmatch("[^,%s]+") do
    if not known[skill] then
      table.insert(out, { lnum = skills.lnum, col = skills.col, severity = WARN, message = skill .. " is not a skill here" })
    end
  end
  if s.prompt:sub(1, 1) == "/" then
    local first = vim.tbl_filter(function(line) return line.text:match("%S") end, s.prompt_lines)[1]
    table.insert(out, { lnum = first.lnum, col = 0, severity = WARN,
      message = "a prompt starting with / would run as a command, so launch puts the transcript line first" })
  end
  for _, line in ipairs(s.prompt_lines) do
    for _, n in ipairs(names(line.text)) do
      if not n.known then
        table.insert(out, { lnum = line.lnum, col = n.col, severity = WARN,
          message = "/" .. n.name .. " is not a skill; it stays in the prompt as text" })
      end
    end
  end
  return out
end

-- The --- lines as bars across the window, the frontmatter's # comments as comments (markdown's YAML would
-- not dim them all), hidden fields not drawn, the detected skills without their # and marked frozen, and a
-- placeholder in an empty prompt.
local function paint(s)
  vim.api.nvim_buf_clear_namespace(buf, draw, 0, -1)
  local win = vim.fn.bufwinid(buf)
  if win == -1 then return end
  vim.api.nvim_set_option_value("conceallevel", 2, { win = win, scope = "local" })
  local width = vim.api.nvim_win_get_width(win) - vim.fn.getwininfo(win)[1].textoff
  for _, lnum in ipairs(s.bars) do
    vim.api.nvim_buf_set_extmark(buf, draw, lnum, 0,
      { virt_text = { { ("━"):rep(width), "FloatBorder" } }, virt_text_pos = "overlay" })
  end
  local function line_len(lnum) return #vim.api.nvim_buf_get_lines(buf, lnum, lnum + 1, false)[1] end
  for _, lnum in ipairs(s.comments) do
    vim.api.nvim_buf_set_extmark(buf, draw, lnum, 0, { end_col = line_len(lnum), hl_group = "Comment" })
  end
  for key, f in pairs(s.fields) do
    if not shown[key] then vim.api.nvim_buf_set_extmark(buf, draw, f.lnum, 0, { conceal_lines = "" }) end
  end
  if s.detected then
    local label = #"# detected skills:"
    vim.api.nvim_buf_set_extmark(buf, draw, s.detected, 0, { end_col = 2, conceal = "" })
    vim.api.nvim_buf_set_extmark(buf, draw, s.detected, 2, { end_col = label, hl_group = "Comment" })
    vim.api.nvim_buf_set_extmark(buf, draw, s.detected, label, { end_col = line_len(s.detected), hl_group = "Special" })
  end
  local first = s.prompt_lines[2] or s.prompt_lines[1] -- under the blank line after the closing ---
  if s.prompt == "" and first then
    vim.api.nvim_buf_set_extmark(buf, draw, first.lnum, 0, { virt_text_pos = "overlay",
      virt_text = { { "your prompt, in markdown; /name anywhere loads a skill. The # lines above say the rest.", "Comment" } } })
  end
end

-- After every edit: apply a newly chosen preset, write the detected skills line again (so an edit to it
-- does not stay), and redraw and recheck.
local last_preset = (shape().fields.preset or {}).value
local function sync()
  local s = shape()
  local preset = s.fields.preset and s.fields.preset.value
  if preset and preset ~= last_preset and presets[preset] then
    apply(last_preset, preset)
    last_preset = preset
    s = shape()
  end
  if s.bars[2] then
    local line = "# detected skills: " .. (#detected(s) > 0 and table.concat(detected(s), " ") or "(none)")
    local after = (s.last_field or 0) + 1
    if s.detected then set_lines(s.detected, s.detected + 1, { line }) else set_lines(after, after, { "", line }) end
    s = shape()
  end
  paint(s)
  vim.diagnostic.set(ns, buf, problems(s))
end

vim.diagnostic.config({ virtual_text = true, signs = true, underline = true, update_in_insert = true }, ns)
vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "TextChangedP", "InsertLeave" }, { buffer = buf, callback = sync })
vim.api.nvim_create_autocmd({ "VimResized", "WinResized" }, { callback = function() paint(shape()) end })

-- Completion: a field's values on a field line, skill names after a / in the prompt or on skills:.
-- Returns the 0-based column the word starts at and the candidates, or nil where there is nothing to offer.
local function candidates()
  local lnum, col = unpack(vim.api.nvim_win_get_cursor(0))
  local before, s = vim.api.nvim_get_current_line():sub(1, col), shape()
  local function skills(start)
    local out = {}
    for skill in pairs(known) do
      local doc = docs[skill] or { "", "" }
      table.insert(out, { word = skill, info = doc[1], doc = ("**%s**\n\n%s\n\n`%s`"):format(skill, doc[1], doc[2]) })
    end
    table.sort(out, function(a, b) return a.word < b.word end)
    return start, out
  end
  if not s.bars[2] or lnum - 1 > s.bars[2] then
    local start = before:match("^()/[%w_%.:%-]*$") or before:match("[%s(\"']()/[%w_%.:%-]*$")
    if start then return skills(start) end -- start is the /, 1-based: the word begins right after it
    return
  end
  local key = before:match("^(%w+):")
  if not key or not s.fields[key] or lnum - 1 >= s.bars[2] then return end
  local start = #before - #before:match("[^,%s:]*$")
  if key == "skills" then return skills(start) end
  local out = {}
  if key == "effort" then -- only what the chosen model supports, each level described
    for _, l in ipairs(levels(s.fields.model and s.fields.model.value or defaults.model)) do
      table.insert(out, { word = l[1], insert = quoted(l[1]), info = l[2], doc = l[2] })
    end
    return start, out
  end
  for _, value in ipairs(values[key] or {}) do
    local preset, info = presets[value], ""
    if key == "preset" and preset then
      local sets = {}
      for _, k in ipairs(values.order) do
        local v = preset.fields[k]
        if v then table.insert(sets, k .. " " .. (v ~= "" and v or "(none)")) end
      end
      if preset.prompt ~= "" then table.insert(sets, "first prompt: " .. vim.split(preset.prompt, "\n")[1]) end
      info = table.concat(sets, " · ")
    elseif key == "model" and models[value] then
      local m = models[value]
      info = (m.provider == "codex" and ("codex %s · "):format(m.id) or "") .. m.description
    end
    table.insert(out, { word = value, insert = quoted(value), info = info, doc = info })
  end
  return start, out
end

local ok_cmp, cmp = pcall(require, "cmp")
if ok_cmp then
  local source = {}
  function source:is_available() return vim.api.nvim_get_current_buf() == buf end
  function source:get_trigger_characters() return { "/" } end
  function source:get_keyword_pattern() return [[\%(\w\|[-.:]\)*]] end
  function source:complete(_, callback)
    local _, found = candidates()
    callback({ isIncomplete = false, items = vim.tbl_map(function(c)
      return { label = c.word, insertText = c.insert, labelDetails = { description = c.info:sub(1, 60) },
        documentation = c.doc ~= "" and { kind = "markdown", value = c.doc } or nil }
    end, found or {}) })
  end
  cmp.register_source("handoff", source)
  -- This source alone: cmp-path, as the fallback group, listed / (usr/, bin/) for any /word that is not a
  -- skill and for anything typed below the bars.
  cmp.setup.buffer({ sources = { { name = "handoff" } } })
else
  function _G.handoff_complete(findstart, base)
    local start, found = candidates()
    if findstart == 1 then return start or -3 end -- -3: cancel quietly
    local words, by_word = {}, {}
    for _, c in ipairs(found or {}) do table.insert(words, c.word); by_word[c.word] = c end
    return vim.tbl_map(function(w)
      local c = by_word[w]
      return { word = c.insert or w, abbr = w, menu = c.info:sub(1, 60), info = c.info }
    end, base == "" and words or vim.fn.matchfuzzy(words, base))
  end
  vim.bo[buf].omnifunc = "v:lua.handoff_complete"
end
-- Tab where there is something to complete opens the list (cmp's own Tab confirms one first); else a Tab.
vim.keymap.set("i", "<Tab>", function()
  if not candidates() then return "<Tab>" end
  if ok_cmp then vim.schedule(cmp.complete); return "" end
  return vim.fn.pumvisible() == 1 and "<C-n>" or "<C-x><C-o>"
end, { buffer = buf, expr = true })

-- gf on a skill name (with or without its /) opens its SKILL.md in a split; anywhere else it is gf.
vim.keymap.set("n", "gf", function()
  local line, col = vim.api.nvim_get_current_line(), vim.fn.col(".")
  if line:sub(col, col) == "/" then col = col + 1 end
  local first, last = col, col
  while first > 1 and line:sub(first - 1, first - 1):match("[%w_%.:%-]") do first = first - 1 end
  while last <= #line and line:sub(last, last):match("[%w_%.:%-]") do last = last + 1 end
  local doc = docs[(line:sub(first, last - 1):gsub("[%.:%-]+$", ""))]
  if not doc then
    local ok, err = pcall(vim.cmd, "normal! gf")
    if not ok then vim.api.nvim_echo({ { err, "ErrorMsg" } }, true, {}) end
    return
  end
  vim.cmd("split " .. vim.fn.fnameescape(doc[2]))
  vim.wo.conceallevel = vim.go.conceallevel
end, { buffer = buf })
-- :wq here quits nvim even with a SKILL.md split open; an unsaved edit in one still stops the quit.
vim.api.nvim_create_autocmd("QuitPre", {
  buffer = buf,
  callback = function()
    for _, win in ipairs(vim.api.nvim_list_wins()) do
      if win ~= vim.api.nvim_get_current_win() then pcall(vim.api.nvim_win_close, win, false) end
    end
  end,
})

-- :wq with an error writes nothing and stays: the buffer is left modified, so the quit is refused too.
vim.api.nvim_create_autocmd("BufWriteCmd", {
  buffer = buf,
  callback = function()
    sync() -- an edit that no TextChanged has seen yet (a :s, a -c command) counts too
    local errors = vim.tbl_filter(function(d) return d.severity == ERROR end, problems(shape()))
    if #errors > 0 then
      vim.bo[buf].modified = true
      vim.schedule(function()
        vim.api.nvim_echo({ { "handoff: not launched; " .. errors[1].message, "ErrorMsg" } }, true, {})
      end)
      return
    end
    vim.fn.writefile(vim.api.nvim_buf_get_lines(buf, 0, -1, false), name)
    vim.bo[buf].modified = false
  end,
})

sync()
-- The cursor starts on the prompt's first line, under the blank one after the closing ---, in insert mode;
-- and again once the UI is up: Lucas's 18:12 press opened somewhere else with a UI attached, which no
-- throwaway server reproduced.
local function to_prompt()
  if vim.api.nvim_get_current_buf() == buf then
    local s = shape()
    local first = s.prompt_lines[2] or s.prompt_lines[1]
    vim.api.nvim_win_set_cursor(0, { first and first.lnum + 1 or 1, 0 })
    vim.cmd.startinsert()
  end
end
to_prompt()
vim.api.nvim_create_autocmd("UIEnter", { once = true, callback = function() vim.schedule(to_prompt) end })
