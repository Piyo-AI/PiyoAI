# App icons

The icon is the chick emoji (U+1F425) from Google's Noto Emoji, licensed under the Apache License 2.0
(Copyright 2013 Google, Inc.; the licence text is in `THIRD-PARTY-NOTICES.txt` at the repository root).

- Source: `2D/svg/emoji_u1f425.svg` at https://github.com/googlefonts/noto-emoji, commit
  `e20cbc2bbec1926686be9f9bee7d1d2cfa1fea0e` (the commit is also pinned in `scripts/licenses.py`).
- It was rendered to a 1024 px PNG with a transparent background and turned into every size here with
  `npx tauri icon <png> -o src-tauri/icons`. Android and iOS output was deleted: Piyo is a desktop app.
- To change the icon: replace these files the same way and update the source above and `NOTO_COMMIT` in
  `scripts/licenses.py`, then regenerate `THIRD-PARTY-NOTICES.txt` (`uv run python ../scripts/licenses.py`).
