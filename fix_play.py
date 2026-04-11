with open("/mnt/c/Users/zhaod/winremote-mcp/src/winremote/__main__.py", "r") as f:
    content = f.read()

old = """        if url and not path:
            # Download to temp file
            suffix = ".wav"
            if ".mp3" in url:
            suffix = ".mp3"
            elif ".ogg" in url:
            suffix = ".ogg"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            urllib.request.urlretrieve(url, tmp.name)
            path = tmp.name"""

new = """        if url and not path:
            # Download to temp file
            suffix = ".wav"
            if ".mp3" in url:
                suffix = ".mp3"
            elif ".ogg" in url:
                suffix = ".ogg"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            urllib.request.urlretrieve(url, tmp.name)
            path = tmp.name"""

content = content.replace(old, new)

with open("/mnt/c/Users/zhaod/winremote-mcp/src/winremote/__main__.py", "w") as f:
    f.write(content)

print("Fixed")
