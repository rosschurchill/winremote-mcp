# winremote-mcp for Hermes

Use winremote-mcp as a remote Windows control MCP server from Hermes Agent. Run the server on the Windows machine you want Hermes to control, then add it as a native MCP server in Hermes.

## 1. Install and start winremote-mcp on Windows

```powershell
pip install winremote-mcp
winremote-mcp --host 0.0.0.0 --port 8090 --auth-key "your-secret-key"
```

For trusted local-only testing, you can omit `--host 0.0.0.0` and `--auth-key`, but remote access should use an auth key.

Optional auto-start on boot:

```powershell
winremote-mcp install
```

## 2. Add the MCP server to Hermes

Edit your Hermes config file and add winremote as a native MCP server:

```yaml
mcp_servers:
  winremote:
    type: streamable-http
    url: http://<windows-ip>:8090/mcp
    headers:
      Authorization: Bearer your-secret-key
```

If Hermes runs on the same Windows machine, you can use stdio instead:

```yaml
mcp_servers:
  winremote:
    command: python
    args: ["-m", "winremote", "--transport", "stdio"]
```

Restart Hermes after editing the config so it discovers the new MCP tools.

## 3. Verify from Hermes

Ask Hermes to use winremote, for example:

> Take a screenshot of the Windows desktop and tell me what is visible.

or:

> Run `Get-ComputerInfo` on the Windows machine through winremote.

## Available capabilities

Once connected, Hermes can use winremote tools for:

- screenshots and annotated screenshots
- clicking, typing, scrolling, and window focus
- PowerShell/CMD execution
- file read/write/upload/download
- process, service, task, registry, and event log management
- OCR and short screen recordings

## Links

- GitHub: https://github.com/dddabtc/winremote-mcp
- PyPI: https://pypi.org/project/winremote-mcp/
