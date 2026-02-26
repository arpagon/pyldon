---
name: agent-browser
description: "Browser automation for web interaction. Use when you need to open websites, click elements, fill forms, take screenshots, extract text, or interact with any web page. Uses agent-browser CLI with Chromium."
---

# Agent Browser

Headless browser automation via `agent-browser` CLI. Uses accessibility tree snapshots for element targeting.

## Workflow

1. **Open** a page: `agent-browser open <url>`
2. **Snapshot** to see elements: `agent-browser snapshot`
3. **Interact** using refs from snapshot: `agent-browser click @e5`, `agent-browser fill @e3 "text"`
4. **Extract** info: `agent-browser get text @e1`, `agent-browser screenshot page.png`
5. **Close** when done: `agent-browser close`

## Core Commands

```bash
agent-browser open <url>              # Navigate to URL
agent-browser snapshot                # Accessibility tree with refs (best for AI)
agent-browser click <sel>             # Click element by ref
agent-browser fill <sel> <text>       # Clear and fill input
agent-browser type <sel> <text>       # Type into element
agent-browser press <key>             # Press key (Enter, Tab, etc.)
agent-browser scroll <dir> [px]       # Scroll up/down/left/right
agent-browser screenshot [path]       # Take screenshot
agent-browser screenshot --annotate   # Annotated screenshot with numbered labels
agent-browser close                   # Close browser
```

## Get Info

```bash
agent-browser get text <sel>          # Get text content
agent-browser get html <sel>          # Get innerHTML
agent-browser get value <sel>         # Get input value
agent-browser get title               # Get page title
agent-browser get url                 # Get current URL
```

## Find & Act (Semantic)

```bash
agent-browser find role button click --name "Submit"
agent-browser find text "Sign In" click
agent-browser find label "Email" fill "test@test.com"
```

## Wait

```bash
agent-browser wait <selector>         # Wait for element visible
agent-browser wait <ms>               # Wait milliseconds
agent-browser wait --text "Welcome"   # Wait for text to appear
agent-browser wait --load networkidle # Wait for network idle
```

## Tips

- Always `snapshot` after navigation to see available elements
- Use `@ref` selectors from snapshot output (e.g. `@e2`, `@e15`)
- Use `screenshot --annotate` when you need visual context
- `find` commands combine locating + action in one step
- Close the browser when done to free resources
