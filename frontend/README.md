# Admin app prototype

Clickable visual prototype of the Mail Assistant admin app. **Simulated data only**: it is not connected to the database, the server or any real folder.

Run it:

```
cd frontend
npm install
npm run dev
```

Then open the address Vite prints (usually http://localhost:5173).

What it shows: a retro desktop background (pure CSS) with draggable, resizable glass tiles for recipients and files. Loosening actions (approve a recipient, allow a folder, lift a block) ask for confirmation. Tightening actions do not.

Rules reflected in the files tile: a block always wins, sensitive names are always blocked, single files can only be blocked, never allowed separately.

Design lessons carried over from the Playlist Generator: tiles never get an explicit `z-index` (it breaks `backdrop-filter`), and internal dragging uses pointer events.
