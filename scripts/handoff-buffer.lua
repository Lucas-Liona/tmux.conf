-- The handoff launch buffer (handoff.py opens it with +luafile): completion and checks for the header.
-- The allowed values come from <stem>.launch.values.json, which handoff.py writes fresh on every press.
-- Everything here is buffer-local; ~/.config/nvim is not touched.
local buf = vim.api.nvim_get_current_buf()
local name = vim.api.nvim_buf_get_name(buf)
local values = vim.json.decode(table.concat(vim.fn.readfile((name:gsub("%.launch%.md$", ".launch.values.json"))), "\n"))
local ns = vim.api.nvim_create_namespace("handoff")
local CHECKED = { "model", "effort", "others", "old" } -- a bad one blocks :wq; skills only warn

-- "key: value  # comment" -> key, 0-based column where the value starts, value without the comment
local function field(line)
  local key, rest = line:match("^(%w+):(.*)$")
  if not key then return end
  local value = rest:gsub("#.*$", "")
  return key, #key + 1 + #rest:match("^%s*"), vim.trim(value)
end

local function header()
  local rows = {}
  for i, line in ipairs(vim.api.nvim_buf_get_lines(buf, 0, -1, false)) do
    if line == "---" then break end
    local key, col, value = field(line)
    if key then rows[key] = { lnum = i - 1, col = col, value = value } end
  end
  return rows
end

local function problems()
  local out, rows = {}, header()
  for _, key in ipairs(CHECKED) do
    local row, allowed = rows[key], values[key] or {}
    local full_model_id = key == "model" and row and row.value:match("^claude%-")
    if #allowed > 0 and not (row and vim.tbl_contains(allowed, row.value)) and not full_model_id then
      table.insert(out, {
        lnum = row and row.lnum or 0, col = row and row.col or 0, severity = vim.diagnostic.severity.ERROR,
        message = ("%s: %s is not one of %s"):format(key, row and row.value ~= "" and row.value or "(missing)",
          table.concat(allowed, " ")),
      })
    end
  end
  local skills = rows.skills
  for skill in (skills and skills.value or ""):gmatch("[^,%s]+") do
    -- plugin and claude.ai skills are fine to name too; only ~/.claude/skills is known here
    if not vim.tbl_contains(values.skills, skill) and not skill:find(":") then
      table.insert(out, { lnum = skills.lnum, col = skills.col, severity = vim.diagnostic.severity.WARN,
        message = skill .. " is not in ~/.claude/skills (fine if it is a plugin or claude.ai skill)" })
    end
  end
  return out
end

vim.diagnostic.config({ virtual_text = true, signs = true, underline = true, update_in_insert = true }, ns)
local function check() vim.diagnostic.set(ns, buf, problems()) end
vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "InsertLeave" }, { buffer = buf, callback = check })
check()

-- Completion: the value under the cursor on a header line; on skills:, the name after the last comma.
function _G.handoff_complete(findstart, base)
  local line = vim.api.nvim_get_current_line()
  local key = line:match("^(%w+):")
  if findstart == 1 then
    if not key or not values[key] then return -3 end -- -3: cancel quietly
    local col = vim.api.nvim_win_get_cursor(0)[2]
    local start = col
    while start > #key + 1 and not line:sub(start, start):match("[,%s:]") do start = start - 1 end
    return start
  end
  local out = {}
  for _, v in ipairs(values[key] or {}) do
    if v:sub(1, #base) == base then table.insert(out, v) end
  end
  return out
end
vim.bo[buf].omnifunc = "v:lua.handoff_complete"
-- Tab on a header line opens the list (or moves down it); anywhere else it is a Tab.
vim.keymap.set("i", "<Tab>", function()
  if vim.fn.pumvisible() == 1 then return "<C-n>" end
  return vim.api.nvim_get_current_line():match("^%w+:") and "<C-x><C-o>" or "<Tab>"
end, { buffer = buf, expr = true })

-- The note: everything under ---, the new session's first prompt. It opens as a few real blank lines
-- with a hint drawn over them (virtual text, never written), and the cursor waits on the first one.
local SPACE, hint_ns = 4, vim.api.nvim_create_namespace("handoff-hint")
local HINT = {
  "Write the new session's first prompt here.",
  "The transcript path and the skills line go after it; left empty, it gets context only.",
  ":wq launches · :cq aborts · Tab completes the values above",
}
local function divider()
  for i, line in ipairs(vim.api.nvim_buf_get_lines(buf, 0, -1, false)) do
    if line == "---" then return i end
  end
end

local function hint()
  vim.api.nvim_buf_clear_namespace(buf, hint_ns, 0, -1)
  local d = divider()
  if not d then return end
  local note = vim.api.nvim_buf_get_lines(buf, d, -1, false)
  if table.concat(note):match("%S") then return end
  for i = 1, math.min(#HINT, #note) do
    vim.api.nvim_buf_set_extmark(buf, hint_ns, d + i - 1, 0,
      { virt_text = { { HINT[i], "Comment" } }, virt_text_pos = "overlay" })
  end
end

local d = divider()
if d and vim.api.nvim_buf_line_count(buf) - d < SPACE then
  vim.api.nvim_buf_set_lines(buf, -1, -1, false, vim.fn["repeat"]({ "" }, SPACE - (vim.api.nvim_buf_line_count(buf) - d)))
  vim.bo[buf].modified = false
end
hint()
vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, { buffer = buf, callback = hint })

-- +$ alone is not enough: Lucas's 18:12 press opened at 1:1 with a UI attached, which no throwaway
-- server reproduces. So the cursor is placed again once the UI is up.
local function to_note()
  local line = divider()
  if line and vim.api.nvim_get_current_buf() == buf then
    vim.api.nvim_win_set_cursor(0, { line + 1, 0 })
    vim.cmd.startinsert()
  end
end
to_note()
vim.api.nvim_create_autocmd("UIEnter", { once = true, callback = function() vim.schedule(to_note) end })

-- Until that 1:1 has a name: for 3 s, every key and any jump above the note go to <stem>.launch.keys.log,
-- so the next time it happens it names itself (a mouse event, a terminal reply read as keys, ...).
local started, log_ns = vim.uv.hrtime(), vim.api.nvim_create_namespace("handoff-keylog")
local function note_down(what)
  local ms = (vim.uv.hrtime() - started) / 1e6
  if ms > 3000 then return end
  vim.fn.writefile({ ("%5d ms  %s"):format(ms, what) }, (name:gsub("%.launch%.md$", ".launch.keys.log")), "a")
end
vim.on_key(function(_, typed) if typed ~= "" then note_down("key " .. vim.fn.keytrans(typed)) end end, log_ns)
vim.defer_fn(function() vim.on_key(nil, log_ns) end, 3000)
vim.api.nvim_create_autocmd({ "CursorMoved", "CursorMovedI" }, {
  buffer = buf,
  callback = function()
    local row, line = vim.api.nvim_win_get_cursor(0)[1], divider()
    if line and row <= line then note_down(("cursor to %d:%d, mode %s"):format(row, vim.fn.col("."), vim.fn.mode())) end
  end,
})

-- :wq with an error writes nothing and stays: the buffer is left modified, so the quit is refused too.
vim.api.nvim_create_autocmd("BufWriteCmd", {
  buffer = buf,
  callback = function()
    local errors = vim.tbl_filter(function(d) return d.severity == vim.diagnostic.severity.ERROR end, problems())
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
