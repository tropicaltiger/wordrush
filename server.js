// WordRush: 2-player scrambled word race. Rooms live in memory only (no database).
const http = require('http'), fs = require('fs'), path = require('path');
const { WebSocketServer } = require('ws');

// level -> words as "word|emoji|meaning|synonyms"
const WORDS = {
  1: ['cake|🍰|A sweet baked dessert, often for celebrations|dessert,pastry','moon|🌙|The bright body that circles Earth at night|satellite,luna','fish|🐟|An animal that lives and breathes in water|angler,catch','tree|🌳|A tall plant with a trunk and branches|plant,timber','bird|🐦|An animal with feathers and wings|fowl,flyer','rain|🌧️|Water falling from clouds|shower,drizzle','ship|🚢|A large boat for sea travel|vessel,liner','book|📖|Pages bound together for reading|volume,novel'],
  2: ['apple|🍎|A round fruit, red or green, that grows on trees|fruit,pome','plane|✈️|A flying vehicle with wings|jet,aircraft','tiger|🐅|A big striped wild cat|big cat,predator','music|🎵|Organised sounds that people sing or play|melody,tunes','river|🏞️|A large stream of water flowing to the sea|stream,waterway','clock|🕒|A device that shows the time|timepiece,watch','bread|🍞|Baked food made from flour and water|loaf,bun','smile|😊|A happy look with the corners of the mouth turned up|grin,beam'],
  3: ['garden|🌷|A place where flowers or vegetables are grown|yard,plot','planet|🪐|A large body that orbits a star|world,globe','friend|🤝|A person you like and trust|pal,buddy','rocket|🚀|A vehicle that travels into space|missile,spacecraft','castle|🏰|A large fortified building of old kings|fort,palace','banana|🍌|A long curved yellow fruit|plantain','doctor|🩺|A person trained to treat sick people|physician,medic','winter|❄️|The coldest season of the year|cold season'],
  4: ['pyramid|🔺|A shape with a square base and triangle sides|monument,tomb','journey|🧳|A long trip from one place to another|voyage,trip','monster|👹|A frightening imaginary creature|beast,giant','captain|🧑‍✈️|The leader of a ship, plane or team|skipper,chief','volcano|🌋|A mountain that can erupt with lava|crater,vent','elephant|🐘|The largest land animal, with a trunk|jumbo,pachyderm','diamond|💎|A very hard, sparkling precious stone|gem,jewel','harvest|🌾|Gathering of ripe crops|reap,yield'],
  5: ['adventure|🧭|An exciting or risky experience|quest,expedition','telescope|🔭|A tool for seeing faraway objects in the sky|scope,spyglass','beautiful|🌺|Very pleasing to look at|lovely,gorgeous','chocolate|🍫|A sweet food made from cocoa|cocoa,candy','umbrella|☂️|A cover held over you to keep off rain|parasol,brolly','mountain|🏔️|A very high natural hill|peak,summit','dinosaur|🦖|A giant reptile that lived millions of years ago|prehistoric reptile','triangle|🔺|A shape with three sides|trigon']
};
const SECS = [40, 50, 55, 60, 60], ROUNDS = 5, PAUSE = 7000;
const rooms = new Map();

const pick = l => { const [word, emoji, meaning, syn] = WORDS[l][Math.floor(Math.random() * WORDS[l].length)].split('|'); return { word, emoji, meaning, syn: syn.split(',') }; };
const scramble = w => { let s; do { s = w.split('').sort(() => Math.random() - .5).join(''); } while (s === w); return s.toUpperCase().split(''); };
const send = (ws, o) => ws.readyState === 1 && ws.send(JSON.stringify(o));
const bcast = (r, o) => r.players.forEach(p => send(p.ws, o));
const scores = r => r.players.map(p => p.score);
const code = () => { let c; do { c = Math.random().toString(36).slice(2, 6).toUpperCase(); } while (rooms.has(c)); return c; };

function startRound(r) {
  r.idx++;
  if (r.idx >= ROUNDS) { bcast(r, { t: 'end', scores: scores(r), names: r.players.map(p => p.name) }); return; }
  r.cur = pick(r.idx + 1); r.solved = false;
  r.ends = Date.now() + SECS[r.idx] * 1000;
  bcast(r, { t: 'round', n: r.idx + 1, total: ROUNDS, level: r.idx + 1, letters: scramble(r.cur.word), secs: SECS[r.idx], scores: scores(r) });
  r.timer = setTimeout(() => finish(r, -1), SECS[r.idx] * 1000 + 300);
}
function finish(r, who) {
  if (r.solved) return; r.solved = true; clearTimeout(r.timer);
  let pts = 0;
  if (who >= 0) { pts = (r.idx + 1) * 10 + Math.max(0, Math.ceil((r.ends - Date.now()) / 1000)); r.players[who].score += pts; }
  const c = r.cur;
  bcast(r, { t: 'result', by: who, pts, word: c.word.toUpperCase(), emoji: c.emoji, meaning: c.meaning, syn: c.syn, scores: scores(r), last: r.idx === ROUNDS - 1, pause: PAUSE });
  r.next = setTimeout(() => startRound(r), PAUSE);
}

const server = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  fs.createReadStream(path.join(__dirname, 'public', 'index.html')).pipe(res);
});
const wss = new WebSocketServer({ server });

wss.on('connection', ws => {
  ws.on('message', raw => {
    let m; try { m = JSON.parse(raw); } catch { return; }
    if (m.t === 'create') {
      const c = code(); const r = { code: c, players: [{ ws, name: (m.name || 'Player 1').slice(0, 14), score: 0 }], idx: -1 };
      rooms.set(c, r); ws.room = r; ws.pi = 0; send(ws, { t: 'created', code: c });
    } else if (m.t === 'join') {
      const r = rooms.get((m.code || '').toUpperCase());
      if (!r) return send(ws, { t: 'error', msg: 'Room not found. Check the code.' });
      if (r.players.length >= 2) return send(ws, { t: 'error', msg: 'This room is already full.' });
      r.players.push({ ws, name: (m.name || 'Player 2').slice(0, 14), score: 0 }); ws.room = r; ws.pi = 1;
      const names = r.players.map(p => p.name);
      r.players.forEach((p, i) => send(p.ws, { t: 'start', names, you: i }));
      setTimeout(() => startRound(r), 2500);
    } else if (m.t === 'guess') {
      const r = ws.room; if (!r || r.solved || !r.cur) return;
      if (String(m.w).toLowerCase() === r.cur.word) finish(r, ws.pi); else send(ws, { t: 'wrong' });
    }
  });
  ws.on('close', () => {
    const r = ws.room; if (!r) return;
    clearTimeout(r.timer); clearTimeout(r.next);
    r.players.forEach(p => p !== r.players[ws.pi] && send(p.ws, { t: 'left' }));
    rooms.delete(r.code);
  });
});
server.listen(process.env.PORT || 3000, () => console.log('WordRush running'));
