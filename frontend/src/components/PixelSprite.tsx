// Draws a tiny pixel image from text rows. Own artwork, no borrowed icons.
export function PixelSprite({
  rows,
  palette,
  scale = 3,
}: {
  rows: string[]
  palette: Record<string, string>
  scale?: number
}) {
  const w = Math.max(...rows.map((r) => r.length))
  return (
    <svg width={w * scale} height={rows.length * scale} viewBox={`0 0 ${w} ${rows.length}`} shapeRendering="crispEdges" aria-hidden>
      {rows.flatMap((row, y) =>
        [...row].map((ch, x) => (palette[ch] ? <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={palette[ch]} /> : null)),
      )}
    </svg>
  )
}

export const FOLDER = {
  palette: { K: '#3a3a4a', Y: '#f5d442', O: '#c9a21d' },
  rows: [
    '.KKKKKK.........',
    '.KYYYYYK........',
    '.KYYYYYYKKKKKKK.',
    '.KYYYYYYYYYYYYYK',
    '.KYYYYYYYYYYYYYK',
    '.KYYYYYYYYYYYYYK',
    '.KYYYYYYYYYYYYYK',
    '.KYYYYYYYYYYYYYK',
    '.KYYYYYYYYYYYYYK',
    '.KOOOOOOOOOOOOOK',
    '.KKKKKKKKKKKKKKK',
  ],
}

export const ENVELOPE = {
  palette: { K: '#3a3a4a', W: '#ffffff', R: '#e0485a' },
  rows: [
    'KKKKKKKKKKKKKKKK',
    'KWWWWWWWWWWWWWWK',
    'KKWWWWWWWWWWWWKK',
    'KWKKWWWWWWWWKKWK',
    'KWWWKKWWWWKKWWWK',
    'KWWWWWKKKKWWWWWK',
    'KWWWWWWWWWWWWWWK',
    'KWWWWWWWWWWWWRRK',
    'KWWWWWWWWWWWWRRK',
    'KWWWWWWWWWWWWWWK',
    'KKKKKKKKKKKKKKKK',
  ],
}

// Postausgang: a tray with a letter lying in it.
export const OUTBOX = {
  palette: { K: '#3a3a4a', W: '#ffffff', R: '#e0485a', G: '#aab0b8', D: '#656c75' },
  rows: [
    '...KKKKKKKK...',
    '...KWWWWWWK...',
    '...KKWWWWKK...',
    '...KWKKKKWK...',
    '...KWWWWWRK...',
    '...KWWWWWRK...',
    'KKKKKKKKKKKKKK',
    'KGGGGGGGGGGGGK',
    'KGDDDDDDDDDDGK',
    'KGDDDDDDDDDDGK',
    'KGGGGGGGGGGGGK',
    'KKKKKKKKKKKKKK',
  ],
}

// Magic wand for the setup wizard.
export const WAND = {
  palette: { K: '#3a3a4a', P: '#7a4bc8', Y: '#ffd23f', W: '#ffffff' },
  rows: [
    '..Y.......Y...',
    '.YWY.....YWY..',
    '..Y.....YYWYY.',
    '..........YWY.',
    '.......KK.Y...',
    '......KPPK....',
    '.....KPPK.....',
    '....KPPK......',
    '...KPPK.......',
    '..KPPK........',
    '.KPPK.........',
    '.KPK..........',
    '..K...........',
  ],
}

export const BIN = {
  palette: { K: '#3a3a4a', G: '#aab0b8', D: '#656c75' },
  rows: [
    '....KKKKKK....',
    'KKKKKKKKKKKKKK',
    'KGGGGGGGGGGGGK',
    'KKKKKKKKKKKKKK',
    '.KGDGDGDGDGDK.',
    '.KGDGDGDGDGDK.',
    '.KGDGDGDGDGDK.',
    '.KGDGDGDGDGDK.',
    '.KGDGDGDGDGDK.',
    '.KGDGDGDGDGDK.',
    '.KGDGDGDGDGDK.',
    '..KGDGDGDGDK..',
    '..KKKKKKKKKK..',
  ],
}

export const CLOUD = {
  palette: { W: '#ffffff', L: '#d6e6f5' },
  rows: [
    '......WWWW..........',
    '....WWWWWWWW...WW...',
    '..WWWWWWWWWWWWWWWWW.',
    '.WWWWWWWWWWWWWWWWWWW',
    '.LLLLLLLLLLLLLLLLLL.',
  ],
}

export const GLOBE = {
  palette: { K: '#3a3a4a', B: '#2f6fd0', G: '#3fae5f' },
  rows: [
    '....KKKKKK....',
    '..KKBBBBGGKK..',
    '.KBBGGBBBGGGK.',
    '.KBGGGBBBBGGK.',
    'KBBGGGGBBBBBBK',
    'KBBBGGGBBBBBBK',
    'KBBBBBBBBGGBBK',
    'KBBBBBBBGGGBBK',
    '.KBBBBBBGGGBK.',
    '.KBBGBBBBGBBK.',
    '..KKBBBBBBKK..',
    '....KKKKKK....',
  ],
}

export const COFFEE = {
  palette: { K: '#3a3a4a', W: '#ffffff', C: '#6b3e1d', S: '#aab0b8' },
  rows: [
    '..S..S......',
    '...S..S.....',
    'KKKKKKKKKK..',
    'KCCCCCCCCKKK',
    'KWWWWWWWWK.K',
    'KWWWWWWWWK.K',
    'KWWWWWWWWKKK',
    '.KWWWWWWWK..',
    '..KKKKKKK...',
  ],
}

export const HELP = {
  palette: { K: '#3a3a4a', B: '#2f6fd0', W: '#ffffff' },
  rows: [
    'KKKKKKKKKKKK',
    'KBBBBBBBBBBK',
    'KBBBWWWWBBBK',
    'KBBWWBBWWBBK',
    'KBBBBBBWWBBK',
    'KBBBBWWWBBBK',
    'KBBBBWWBBBBK',
    'KBBBBWWBBBBK',
    'KBBBBBBBBBBK',
    'KBBBBWWBBBBK',
    'KBBBBBBBBBBK',
    'KKKKKKKKKKKK',
  ],
}

export const POWER = {
  palette: { K: '#3a3a4a', R: '#d6334c', W: '#ffffff' },
  rows: [
    'KKKKKKKKKK',
    'KRRRWWRRRK',
    'KRRRWWRRRK',
    'KRWRWWRWRK',
    'KRWRWWRWRK',
    'KRWRRRRWRK',
    'KRRWWWWRRK',
    'KRRRRRRRRK',
    'KKKKKKKKKK',
  ],
}

const BASE = { K: '#3a3a4a', W: '#ffffff', G: '#aab0b8', D: '#656c75', B: '#2f6fd0', L: '#9cc8f0', R: '#d6334c', Y: '#f5d442', N: '#3fae5f' }

export const COMPUTER = {
  palette: BASE,
  rows: [
    'KKKKKKKKKKKKKKKK',
    'KGGGGGGGGGGGGGGK',
    'KGKKKKKKKKKKKKGK',
    'KGKLLLLLLLLLLKGK',
    'KGKLLLLLLLLLLKGK',
    'KGKLLLBBBLLLLKGK',
    'KGKLLLLLLLLLLKGK',
    'KGKKKKKKKKKKKKGK',
    'KGGGGGGGGGGGGGGK',
    'KKKKKKKKKKKKKKKK',
    '......KGGK......',
    '..KKKKKGGKKKKK..',
    '.KGGGGGGGGGGGGK.',
    '.KKKKKKKKKKKKKK.',
  ],
}

export const CHAT = {
  palette: BASE,
  rows: [
    '..KKKKKKKKKKKK..',
    '.KWWWWWWWWWWWWK.',
    'KWWWWWWWWWWWWWWK',
    'KWWKKWWKKWWKKWWK',
    'KWWKKWWKKWWKKWWK',
    'KWWWWWWWWWWWWWWK',
    '.KWWWWWWWWWWWWK.',
    '..KKKKWWKKKKKK..',
    '......KWK.......',
    '.......K........',
  ],
}

export const NOTEPAD = {
  palette: BASE,
  rows: [
    'KKKKKKKKKKKKKK..',
    'KBBBBBBBBBBBBK..',
    'KWWWWWWWWWWWWK..',
    'KWKKKKKKKKKWWK..',
    'KWWWWWWWWWWWWK..',
    'KWKKKKKKKKKKWK..',
    'KWWWWWWWWWWWWK..',
    'KWKKKKKKKWWWWK..',
    'KWWWWWWWWWWWWK..',
    'KWKKKKKKKKKWWK..',
    'KWWWWWWWWWWWWK..',
    'KKKKKKKKKKKKKK..',
  ],
}

export const SHEET = {
  palette: BASE,
  rows: [
    'KKKKKKKKKKKKKKKK',
    'KGGGKGGGKGGGKGGK',
    'KKKKKKKKKKKKKKKK',
    'KWWWKWWWKWWWKWWK',
    'KWWWKWWWKWWWKWWK',
    'KKKKKKKKKKKKKKKK',
    'KWWWKWWWKWWWKWWK',
    'KWWWKWWWKWWWKWWK',
    'KKKKKKKKKKKKKKKK',
    'KWWWKWWWKWWWKNNK',
    'KWWWKWWWKWWWKNNK',
    'KKKKKKKKKKKKKKKK',
  ],
}

export const PAINT = {
  palette: { ...BASE, N: '#3fae5f' },
  rows: [
    '....KKKKKKKK....',
    '..KKYYYYYYYYKK..',
    '.KYYYYYYYYYYYYK.',
    'KYYRRYYYYBBYYYYK',
    'KYYRRYYYYBBYYYYK',
    'KYYYYYYYYYYYYYYK',
    'KYNNYYYKKKYYYYYK',
    'KYYYYYKYYYKYYYYK',
    '.KYYYYYKKKYYYYK.',
    '.KYYYYYYYYYYYK..',
    '..KKYYYYYYYKK...',
    '....KKKKKKK.....',
  ],
}

export const FLOPPY = {
  palette: BASE,
  rows: [
    'KKKKKKKKKKKKKKKK',
    'KBBBGGGGGGGBBBBK',
    'KBBBGKKGGGGBBBBK',
    'KBBBGGGGGGGBBBBK',
    'KBBBBBBBBBBBBBBK',
    'KBBBBBBBBBBBBBBK',
    'KBWWWWWWWWWWWWBK',
    'KBWWWWWWWWWWWWBK',
    'KBWKKKKKKWWWWWBK',
    'KBWWWWWWWWWWWWBK',
    'KBWKKKKKKKKKWWBK',
    'KBWWWWWWWWWWWWBK',
    'KBBBBBBBBBBBBBBK',
    'KKKKKKKKKKKKKKKK',
  ],
}

export const SPADE_CARD = {
  palette: BASE,
  rows: [
    '.KKKKKKKKKKKKKK.',
    '.KWWWWWWWWWWWWK.',
    '.KWKWWWWWWWWWWK.',
    '.KWWWWWWKKWWWWK.',
    '.KWWWWWKKKKWWWK.',
    '.KWWWWKKKKKKWWK.',
    '.KWWWKKKKKKKKWK.',
    '.KWWWKKKKKKKKWK.',
    '.KWWWWKKWWKKWWK.',
    '.KWWWWWWKKWWWWK.',
    '.KWWWWWKKKKWWWK.',
    '.KKKKKKKKKKKKKK.',
  ],
}

export const HEART_CARD = {
  palette: BASE,
  rows: [
    '.KKKKKKKKKKKKKK.',
    '.KWWWWWWWWWWWWK.',
    '.KWRWWWWWWWWWWK.',
    '.KWWWWWWWWWWWWK.',
    '.KWWWWRRWWRRWWK.',
    '.KWWWRRRRRRRRWK.',
    '.KWWWRRRRRRRRWK.',
    '.KWWWWRRRRRRWWK.',
    '.KWWWWWRRRRWWWK.',
    '.KWWWWWWRRWWWWK.',
    '.KWWWWWWWWWWWRK.',
    '.KKKKKKKKKKKKKK.',
  ],
}

export const MINE = {
  palette: BASE,
  rows: [
    '......K......',
    '..K...K...K..',
    '...K.KKK.K...',
    '....KKKKK....',
    '.K.KKWWKKK.K.',
    '..KKKKKKKKK..',
    'KKKKKKKKKKKKK',
    '..KKKKKKKKK..',
    '.K.KKKKKKK.K.',
    '...K.KKK.K...',
    '....KKKKK....',
    '..K...K...K..',
    '......K......',
  ],
}

export const CELLS = {
  palette: BASE,
  rows: [
    'KKKK.KKKK.KKKK.KKKK',
    'KGGK.KGGK.KGGK.KWWK',
    'KGGK.KGGK.KGGK.KWRK',
    'KGGK.KGGK.KGGK.KWWK',
    'KGGK.KGGK.KGGK.KWWK',
    'KKKK.KKKK.KKKK.KKKK',
    '...................',
    'KKKK.KKKK.KKKK.KKKK',
    'KWWK.KGGK.KGGK.KGGK',
    'KWKK.KGGK.KGGK.KGGK',
    'KWWK.KGGK.KGGK.KGGK',
    'KKKK.KKKK.KKKK.KKKK',
  ],
}

export const PAWN = {
  palette: BASE,
  rows: [
    '....KKKK....',
    '...KWWWWK...',
    '...KWWWWK...',
    '....KWWK....',
    '...KKWWKK...',
    '....KWWK....',
    '....KWWK....',
    '...KWWWWK...',
    '..KWWWWWWK..',
    '..KKKKKKKK..',
    '..KWWWWWWK..',
    '.KWWWWWWWWK.',
    '.KKKKKKKKKK.',
  ],
}

// Desktop icon of the junk-mail game: one of our blob figures.
export const BLOB = {
  palette: { K: '#2a2a3a', G: '#d6334c', W: '#ffffff' },
  rows: [
    '..K........K..',
    '...K......K...',
    '..KKKKKKKKKK..',
    '.KGGGGGGGGGGK.',
    'KGGWWGGGGWWGGK',
    'KGGWKGGGGWKGGK',
    'KGGGGGGGGGGGGK',
    '.KGGKKKKKKGGK.',
    '.KK..K..K..KK.',
    '.K...K..K...K.',
  ],
}
