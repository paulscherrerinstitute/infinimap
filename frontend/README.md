# ibfabric-frontend

React + Cytoscape.js prototype that renders an InfiniBand fabric snapshot as an
interactive topology map, colored by link/node health.

It consumes `public/fabric.json` - the JSON contract emitted by
`ibfabric.serialize.to_dict` (see `../ibfabric`). Nothing here talks to Python
directly. The export is a static file for now (might upgrade to a full backend + db later).

## Run

```bash
# 1. generate the data from a snapshot (from ../ibfabric)
python -m ibfabric.export /.../ibdiagnet2.db_csv frontend/public/fabric.json

# 2. start the dev server
cd frontend
npm install
npm run dev            # http://localhost:5173
```

## Layout

- `src/types.ts` - TypeScript mirror of the JSON contract. Keep in sync with
  `serialize.py`.
- `src/cy/` - the Cytoscape layer: `adapter.ts` (contract -> elements),
  `style.ts` (stylesheet), `layout.ts` (fcose config), `palette.ts` (shared status colors).
- `src/components/` - `GraphView` owns the Cytoscape instance; `DetailPanel`
  renders node/link detail from the `detail` lookup; `Header`, `Toolbar`.
- `src/App.tsx` - loads `fabric.json`, holds selection + filter state.

## Interaction

- Click a node -> ports, firmware, cables, per-port error counts.
- Click a link -> status, the reason it isn't ok (checks / error counters), both
  endpoints' port detail.
- Toolbar: fit, re-run layout, dim-healthy, hide leaf HCAs.

## Known issues (prototype)

- `speed`/`width` are raw ibdiagnet enum ints - a decode map ("NDR" -> "4x") would be nice to compare numerical values.
- `fabric.json` is ~7.5 MB (carries the full `raw` counter bags); fine for now, later we can move `detail` to an on-demand API.
- fcose gives a readable but non-hierarchical layout; a spine/leaf-aware layered layout would better reflect fat-tree structure. In practice, we can just give the user the ability to set his own layout.
