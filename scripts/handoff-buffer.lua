-- The handoff launch buffer (handoff.py opens it with +luafile): the prompt on top, the fields and the
-- detected skills between two bars, and # lines that launch drops. What it needs comes from
-- <stem>.launch.values.json, which handoff.py writes fresh on every press: handoff.toml, the presets, and
-- each skill's description and path. Everything here is buffer-local; ~/.config/nvim is not touched.
local buf = vim.api.nvim_get_current_buf()
local name = vim.api.nvim_buf_get_name(buf)
local values = vim.json.decode(table.concat(vim.fn.readfile((name:gsub("%.launch%.md$", ".launch.values.json"))), "\n"))
local ns = vim.api.nvim_create_namespace("handoff")
local draw = vim.api.nvim_create_namespace("handoff-draw")
local ERROR, WARN = vim.diagnostic.severity.ERROR, vim.diagnostic.severity.WARN
local CHECKED = { "model", "effort", "preset", "others", "old" } -- a bad one blocks :wq; skills only warn
local docs, presets, defaults = values.skill_docs, values.presets, values.defaults
local known, shown = {}, {}
for _, skill in ipairs(values.skills) do known[skill] = true end
for skill in pairs(docs) do known[skill] = true end
for _, key in ipairs(values.config.show) do shown[key] = true end

-- Not markdown: its # lines would be drawn as headings.
vim.bo[buf].filetype = "handoff"
pcall(vim.treesitter.stop, buf)
vim.cmd([[syntax match handoffComment /^#.*/ | highlight default link handoffComment Comment]])

local function is_bar(line) return line ~= "" and line:gsub("━", "") == "" end
local function row(key, value) return (("%-8s%s"):format(key .. ":", value):gsub("%s+$", "")) end -- as handoff.py's

-- The buffer by line (0-based), read the way handoff.py's parse_buffer reads it: # lines dropped, the prompt
-- above the first bar, `key: value` fields up to the second. Also where the detected skills line and the
-- last field are.
local function shape()
  local s, prompt = { bars = {}, fields = {}, prompt_lines = {} }, {}
  for i, line in ipairs(vim.api.nvim_buf_get_lines(buf, 0, -1, false)) do
    if line:sub(1, 1) == "#" then
    elseif is_bar(line) and #s.bars < 2 then
      table.insert(s.bars, i - 1)
    elseif #s.bars == 0 then
      table.insert(prompt, line)
      table.insert(s.prompt_lines, { lnum = i - 1, text = line })
    elseif #s.bars == 1 and line:match("^detected skills:") then
      s.detected = s.detected or i - 1
    elseif #s.bars == 1 then
      local key, rest = line:match("^(%w+):(.*)$")
      if key then
        s.fields[key] = { lnum = i - 1, col = #key + 1 + #rest:match("^%s*"), value = vim.trim(rest) }
        s.last_field = i - 1
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
        local after = (s.last_field or s.bars[1]) + 1
        set_lines(after, after, { row(key, value) })
      end
    end
  end
  local s = shape()
  if s.prompt == "" or s.prompt == vim.trim(old.prompt) then
    set_lines(0, s.bars[1], new.prompt ~= "" and vim.split(vim.trim(new.prompt), "\n") or { "" })
  elseif new.prompt ~= "" then
    notice(("kept your prompt; %s's own is in handoff-presets.md"):format(to))
  end
end

local function problems(s)
  if #s.bars < 2 then
    return { { lnum = 0, col = 0, severity = ERROR,
      message = "two ━ bars are needed: the prompt above the first, the fields between them" } }
  end
  local out = {}
  for _, key in ipairs(CHECKED) do
    local f, allowed = s.fields[key], values[key] or {}
    local value = f and f.value or defaults[key]
    local full_model_id = key == "model" and value and value:match("^claude%-")
    if #allowed > 0 and not vim.tbl_contains(allowed, value) and not full_model_id then
      table.insert(out, { lnum = f and f.lnum or s.bars[1], col = f and f.col or 0, severity = ERROR,
        message = ("%s: %s is not one of %s"):format(key, value ~= "" and value or "(empty)", table.concat(allowed, " ")) })
    end
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

-- The bars across the window, hidden fields not drawn, the detected skills marked frozen, and a
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
  for key, f in pairs(s.fields) do
    if not shown[key] then vim.api.nvim_buf_set_extmark(buf, draw, f.lnum, 0, { conceal_lines = "" }) end
  end
  if s.detected then
    vim.api.nvim_buf_set_extmark(buf, draw, s.detected, 0, { end_col = #"detected skills:", hl_group = "Comment" })
    vim.api.nvim_buf_set_extmark(buf, draw, s.detected, #"detected skills:",
      { end_col = #vim.api.nvim_buf_get_lines(buf, s.detected, s.detected + 1, false)[1], hl_group = "Special" })
  end
  if s.prompt == "" and s.bars[1] > 0 and vim.api.nvim_buf_get_lines(buf, 0, 1, false)[1] == "" then
    vim.api.nvim_buf_set_extmark(buf, draw, 0, 0, { virt_text_pos = "overlay",
      virt_text = { { "your prompt; /name anywhere loads a skill. The # lines at the bottom say the rest.", "Comment" } } })
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
    local line = "detected skills: " .. (#detected(s) > 0 and table.concat(detected(s), " ") or "(none)")
    if s.detected then set_lines(s.detected, s.detected + 1, { line }) else set_lines(s.bars[2], s.bars[2], { "", line }) end
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
  if s.bars[1] and lnum - 1 < s.bars[1] then
    local start = before:match("^()/[%w_%.:%-]*$") or before:match("[%s(\"']()/[%w_%.:%-]*$")
    if start then return skills(start) end -- start is the /, 1-based: the word begins right after it
    return
  end
  local key = before:match("^(%w+):")
  if not key or not s.fields[key] or s.bars[2] and lnum - 1 > s.bars[2] then return end
  local start = #before - #before:match("[^,%s:]*$")
  if key == "skills" then return skills(start) end
  local out = {}
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
    end
    table.insert(out, { word = value, info = info, doc = info })
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
      return { label = c.word, labelDetails = { description = c.info:sub(1, 40) },
        documentation = c.doc ~= "" and { kind = "markdown", value = c.doc } or nil }
    end, found or {}) })
  end
  cmp.register_source("handoff", source)
  cmp.setup.buffer({ sources = cmp.config.sources({ { name = "handoff" } }, { { name = "path" } }) })
else
  function _G.handoff_complete(findstart, base)
    local start, found = candidates()
    if findstart == 1 then return start or -3 end -- -3: cancel quietly
    local words = vim.tbl_map(function(c) return c.word end, found or {})
    local info = {}
    for _, c in ipairs(found or {}) do info[c.word] = c.info end
    return vim.tbl_map(function(w) return { word = w, menu = info[w]:sub(1, 40), info = info[w] } end,
      base == "" and words or vim.fn.matchfuzzy(words, base))
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
-- The cursor starts in the prompt, in insert mode, and again once the UI is up: Lucas's 18:12 press
-- opened somewhere else with a UI attached, which no throwaway server reproduced.
local function to_prompt()
  if vim.api.nvim_get_current_buf() == buf then
    vim.api.nvim_win_set_cursor(0, { 1, 0 })
    vim.cmd.startinsert()
  end
end
to_prompt()
vim.api.nvim_create_autocmd("UIEnter", { once = true, callback = function() vim.schedule(to_prompt) end })
