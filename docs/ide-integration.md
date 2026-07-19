# Editor integration

`riskratchet` does not (yet) ship a native VS Code extension or JetBrains
plugin. It does emit [SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html),
which both editors can consume today with off-the-shelf viewers. This
gets you inline findings in the Problems panel with file:line jump-to,
no extension code required.

Live, on-save diagnostics via a native LSP server are on the roadmap but
not yet implemented.

## VS Code

### One-time setup

1. Install the [SARIF Viewer](https://marketplace.visualstudio.com/items?itemName=MS-SarifVSCode.sarif-viewer)
   extension by Microsoft DevLabs.
2. Add the task below to `.vscode/tasks.json` in your project (create the
   file if it does not exist):

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "RiskRatchet: scan workspace",
      "type": "shell",
      "command": "riskratchet",
      "args": [
        "scan",
        "${workspaceFolder}/src",
        "--format", "sarif",
        "--output", "${workspaceFolder}/.riskratchet/report.sarif"
      ],
      "problemMatcher": [],
      "presentation": {
        "reveal": "silent",
        "panel": "dedicated"
      }
    }
  ]
}
```

Adjust the `src` path if your package lives elsewhere.

### Daily use

1. Run the task: `Cmd+Shift+P` -> `Tasks: Run Task` -> `RiskRatchet: scan workspace`.
   (Bind it to a key with `cmd+r r` or similar if you run it often.)
2. Open `.riskratchet/report.sarif`. SARIF Viewer takes over and renders
   findings in the Problems panel, grouped by file. Click a finding to
   jump to the function.

### Tips

- Add `.riskratchet/report.sarif` to `.gitignore`.
- Use `--min-score 25` to hide low-risk noise.
- For PR review, the `--format pr-comment` output is friendlier than
  SARIF.

## JetBrains (PyCharm, IntelliJ)

JetBrains SARIF support is uneven across editions. Two paths:

### PyCharm/IntelliJ Ultimate 2024.2+

These bundle a generic LSP client. A native `riskratchet lsp` server is a
**demand-gated** roadmap item (it ships only if real demand appears — see `TODO.md`) and would be
the cleanest integration path if built. Until/unless it does:

1. Run `riskratchet scan src --format sarif --output .riskratchet/report.sarif`
   from the terminal panel.
2. Use the [Qodana plugin](https://plugins.jetbrains.com/plugin/20631-qodana)
   or [SARIF Viewer](https://plugins.jetbrains.com/plugin/22850-sarif-viewer)
   to render the file. Open the report from the tool window.

### PyCharm Community

No first-class SARIF viewer exists for Community edition. The pragmatic
workflow is:

- Run `riskratchet scan src --format table` in the terminal panel.
- Or use `--format github` and run inside a GitHub Actions step instead
  of in-editor.

## Neovim / Helix / Zed / Sublime

These editors all speak LSP and would integrate cleanly *if* a native
`riskratchet lsp` server is built (demand-gated — see `TODO.md`). Until/unless it is, the
`--format github` and `--format pr-comment` outputs are the recommended surface — they were
designed for terminal and code review, not editors.

## Other LSP-capable editors

If you want to drive the SARIF flow from any editor's task runner, the
contract is:

- Command: `riskratchet scan <paths> --format sarif --output <file>`
- Exit code: zero on success regardless of findings (the SARIF file
  carries the findings).
- Output: a valid SARIF 2.1.0 log; `runs[].results[]` carries one entry
  per finding with `locations[].physicalLocation.region.startLine` /
  `endLine` pointing at the function span.
