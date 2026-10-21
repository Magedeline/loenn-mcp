using System;
using System.Collections.Generic;
using System.Linq;

namespace CelPcg
{
    public sealed class GenRequest
    {
        public int Width = 40, Height = 23;              // tiles
        public List<Exit> Exits = new List<Exit>();
        public int Entry;                                 // index into Exits; must be a left or right exit
        public int Difficulty = 3;                        // 1..10
        public ulong Seed = 1;
        public char FgChar = '1', BgChar = '1';
        public int MaxAttempts = 400;
    }

    public sealed class GenResult
    {
        public Room Room = null!;
        public ValidationReport Report = null!;
        public int Attempts;
        public ulong AttemptSeed;
        public const string Generator = "celpcg/0.1/platform-chain";
    }

    /// <summary>
    /// Gray-box challenge rooms: walls and floor, the requested exits with landing ledges, a
    /// chain of platforms from the entry to every other exit, and hazards scaled by difficulty.
    /// A room is only returned when the validator proves every exit reachable; failed attempts
    /// retry with seeds derived from the request seed, so the same request gives the same room.
    /// Only vanilla entities are placed (player, spikesUp, jumpThru, spring, refill).
    /// </summary>
    public static class PlatformChainGenerator
    {
        public const int FloorRows = 3, WallCols = 2, CeilingRows = 2;

        public static GenResult Generate(GenRequest req)
        {
            if (req.Exits.Count == 0) throw new ArgumentException("give at least one exit");
            if (req.Entry < 0 || req.Entry >= req.Exits.Count) throw new ArgumentException("entry must index an exit");
            var entry = req.Exits[req.Entry];
            if (entry.Side != Side.Left && entry.Side != Side.Right)
                throw new ArgumentException("the entry must be a left or right exit in this version");
            int d = Math.Max(1, Math.Min(10, req.Difficulty));
            ValidationReport? last = null;
            Room? lastRoom = null;
            for (int attempt = 0; attempt < req.MaxAttempts; attempt++)
            {
                ulong s = attempt == 0 ? req.Seed : Seed.Derive(req.Seed, "attempt", attempt);
                var room = Build(req, d, new Pcg32(s));
                var rep = Validator.Check(room);
                if (rep.Ok)
                    return new GenResult { Room = room, Report = rep, Attempts = attempt + 1, AttemptSeed = s };
                last = rep; lastRoom = room;
            }
            throw new InvalidOperationException(
                $"no valid room in {req.MaxAttempts} attempts; last problems: {string.Join("; ", last!.Issues)}\n{lastRoom!.Ascii()}");
        }

        private static (int x, int y) Target(Room room, Exit e)
        {
            int floorTop = room.Height - FloorRows;
            return e.Side switch
            {
                Side.Left => (0, e.Offset + e.Size - 1),
                Side.Right => (room.Width - 1, e.Offset + e.Size - 1),
                Side.Top => (e.Offset + e.Size / 2, 0),
                _ => (e.Offset + e.Size / 2, floorTop - 1),
            };
        }

        private static Room Build(GenRequest req, int d, Pcg32 rng)
        {
            var room = new Room(req.Width, req.Height) { FgChar = req.FgChar, BgChar = req.BgChar };
            room.Exits.AddRange(req.Exits.Select(e => new Exit(e.Side, e.Offset, e.Size, e.Kind)));
            room.EntryIndex = req.Entry;
            int W = room.Width, H = room.Height, floorTop = H - FloorRows;

            // Shell.
            room.Fill(0, 0, WallCols, H, '#');
            room.Fill(W - WallCols, 0, WallCols, H, '#');
            room.Fill(0, 0, W, CeilingRows, '#');
            room.Fill(0, floorTop, W, FloorRows, '#');

            // Openings, with a ledge to land on under left/right exits.
            var protectedCols = new HashSet<int>();
            var keepClear = new HashSet<(int, int)>();   // corridors in front of openings stay empty
            foreach (var e in room.Exits)
            {
                switch (e.Side)
                {
                    case Side.Left:
                    case Side.Right:
                    {
                        int x0 = e.Side == Side.Left ? 0 : W - WallCols;
                        room.Fill(x0, e.Offset, WallCols, e.Size, '.');
                        int ledgeY = e.Offset + e.Size;
                        int lx = e.Side == Side.Left ? 0 : W - 4;
                        if (ledgeY < floorTop) room.Fill(lx, ledgeY, 4, 1, '#');
                        for (int k = 0; k < 6; k++) protectedCols.Add(e.Side == Side.Left ? k : W - 1 - k);
                        for (int k = 0; k < 6; k++)
                            for (int r = e.Offset - 1; r < e.Offset + e.Size; r++)
                                keepClear.Add((e.Side == Side.Left ? k : W - 1 - k, r));
                        break;
                    }
                    case Side.Top:
                        room.Fill(e.Offset, 0, e.Size, CeilingRows, '.');
                        for (int k = e.Offset; k < e.Offset + e.Size; k++)
                            for (int r = CeilingRows; r < CeilingRows + 2; r++) keepClear.Add((k, r));
                        break;
                    default:
                        room.Fill(e.Offset, floorTop, e.Size, FloorRows, '.');
                        for (int k = e.Offset - 1; k <= e.Offset + e.Size; k++) protectedCols.Add(k);
                        break;
                }
            }

            // Spawn: standing just inside the entry, on its ledge (or the floor).
            var entry = room.Entry;
            int sx = entry.Side == Side.Left ? 2 : W - 3;
            int sy = entry.Offset + entry.Size - 1;
            room.SpawnX = sx; room.SpawnY = sy;

            // Platform chains towards every other exit.
            int maxHop = Math.Min(Reach.JumpSide, 3 + d / 3);
            int minHop = Math.Max(2, maxHop - 3);
            int refills = 0;
            for (int i = 0; i < room.Exits.Count; i++)
            {
                if (i == room.EntryIndex) continue;
                var (tx, ty) = Target(room, room.Exits[i]);
                int cx = sx, cy = sy, guard = 0;
                while (guard++ < 40)
                {
                    int dx = tx - cx, dy = ty - cy;
                    if (Math.Abs(dx) <= maxHop + 1 && dy >= -Reach.JumpRise && dy <= 6) break;
                    int dir = dx == 0 ? (rng.Chance(0.5) ? 1 : -1) : Math.Sign(dx);
                    int hop = Math.Abs(dx) <= 2 && dy < -Reach.JumpRise ? rng.Range(0, 2) : rng.Range(minHop, maxHop);
                    int nx = Math.Max(WallCols + 1, Math.Min(W - WallCols - 2, cx + dir * hop));
                    int rise = dy < -1 ? -rng.Range(2, Reach.JumpRise) : dy > 2 ? rng.Range(0, 3) : rng.Range(-1, 1);
                    int ny = Math.Max(CeilingRows + 2, Math.Min(floorTop - 2, cy + rise));
                    int pw = Math.Max(1, rng.Range(2, 5 - d / 4));
                    char solid = d >= 3 && rng.Chance(0.15 + d * 0.03) ? '=' : '#';
                    int px = Math.Max(WallCols, Math.Min(W - WallCols - pw, nx - pw / 2));
                    for (int k = 0; k < pw; k++)
                    {
                        char under = room.At(px + k, ny + 1);
                        if ((under == '.' || under == '*') && !keepClear.Contains((px + k, ny + 1)))
                        {
                            if (under == '*') refills--;          // a platform never leaves a refill embedded in it
                            room.Set(px + k, ny + 1, solid);
                        }
                    }
                    // Keep the body space above the platform clear.
                    for (int k = 0; k < pw; k++) { if (room.At(px + k, ny) == '#') room.Set(px + k, ny, '.'); }
                    if (d >= 5 && refills < 2 && ny - 2 > CeilingRows && rng.Chance(0.1 + d * 0.02)
                        && room.At(nx, ny - 2) == '.' && !keepClear.Contains((nx, ny - 2)))
                    {
                        room.Set(nx, ny - 2, '*');
                        refills++;
                    }
                    cx = nx; cy = ny;
                }
                // Big vertical gaps near the target get a spring.
                if (ty < cy - Reach.JumpRise && room.At(cx, cy + 1) == '#') room.Set(cx, cy, 'S');
            }

            // Floor spikes: runs on the floor top, away from spawn ledges and pit exits.
            double spikeChance = (d - 1) / 12.0;
            int y = floorTop - 1;
            for (int x = WallCols; x < W - WallCols; x++)
            {
                if (protectedCols.Contains(x) || room.At(x, y) != '.' || room.At(x, y + 1) != '#') continue;
                if (!rng.Chance(spikeChance)) continue;
                int run = rng.Range(2, 2 + d);
                for (int k = 0; k < run && x + k < W - WallCols; k++)
                {
                    if (protectedCols.Contains(x + k) || room.At(x + k, y) != '.' || room.At(x + k, y + 1) != '#') break;
                    room.Set(x + k, y, '^');
                }
                x += run;
            }
            return room;
        }
    }
}
