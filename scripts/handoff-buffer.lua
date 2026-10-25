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
