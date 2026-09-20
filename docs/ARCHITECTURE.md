# Architecture

Desktop/Browser UI (static SPA) --HTTP--> Flask API --> Profiles JSON (XDG)
                                         \-> Chromium launcher + extension

No Wine. No WebView2. Linux UI via pywebview GTK or system browser fallback.
Cloud proxy / updater are stubs in v1.
