using System.Collections.Generic;

namespace CelPcg
{
    /// <summary>
    /// Tile-level movement model, ported from CEL Tools/Maps/overworld_gen/reach.py.
    /// The player is one tile wide and two tall; a position is the feet tile. Distances
    /// are Madeline's physics rounded down to whole tiles, so the check errs safe:
    ///   jump 3 tiles up, 6 across with air control; dash 4 straight / 3 diagonal;
    ///   spring 6 up and refills the dash; refill restores the dash; falling drifts 1 tile.
    /// Climbing and wall jumps are not modelled: anything accepted is reachable without them.
    /// Added to the Python original: '=' jump-throughs (free from below, a floor from above).
    /// </summary>
    public sealed class Reach
    {
        public const int JumpRise = 3, JumpSide = 6, DashLen = 4, DashDiag = 3, PostDashSide = 2;
        public const int SpringRise = 6, SpringSide = 4;

        private static readonly (int dx, int dy)[] Dirs8 =
            { (1, 0), (-1, 0), (0, -1), (0, 1), (1, -1), (-1, -1), (1, 1), (-1, 1) };

        private readonly Room _g;
        public bool CanDash = true;

        public readonly HashSet<(int x, int y)> Ground = new HashSet<(int, int)>();
        public readonly HashSet<(Side side, int coord)> ExitsReached = new HashSet<(Side, int)>();

        public Reach(Room room) { _g = room; }

        private char At(int c, int r) => r < 0 ? '.' : _g.At(c, r);
        private bool Free(int c, int r) { char ch = At(c, r); return ch != '#' && ch != 'D'; }
        private bool BodyFree(int c, int r) => Free(c, r) && Free(c, r - 1);
        private bool Deadly(int c, int r) => At(c, r) == '^';
        private bool Refill(int c, int r) => At(c, r) == '*' || At(c, r - 1) == '*';
        private bool Floor(int c, int r) { char b = At(c, r + 1); return (b == '#' || b == 'D' || b == '=') && r + 1 < _g.Height; }
        public bool Standable(int c, int r) =>
            c >= 0 && c < _g.Width && r >= 0 && r < _g.Height && BodyFree(c, r) && Floor(c, r) && !Deadly(c, r);

        // State: ground (G) or air (A, with remaining rise, side control, dash, drift).
        private readonly struct S
        {
            public readonly bool Air; public readonly int C, R, Up, Side, Drift; public readonly bool Dash;
            public S(bool air, int c, int r, int up = 0, int side = 0, bool dash = true, int drift = 0)
            { Air = air; C = c; R = r; Up = up; Side = side; Dash = dash; Drift = drift; }
            public long Key => ((long)(Air ? 1 : 0) << 62) | ((long)(C + 2048) << 46) | ((long)(R + 2048) << 30)
                               | ((long)Up << 20) | ((long)Side << 10) | ((long)(Dash ? 1 : 0) << 9) | (long)Drift;
        }

        public void Explore(IEnumerable<(bool air, int c, int r, int up, int side, bool dash, int drift)> starts)
        {
            var seen = new HashSet<long>();
            var q = new Queue<S>();
            void Push(S s) { if (seen.Add(s.Key)) q.Enqueue(s); }
            foreach (var s in starts) Push(new S(s.air, s.c, s.r, s.up, s.side, s.dash, s.drift));

            int? SideMove(int c, int r, int dc)
            {
                int nc = c + dc;
                if (nc < 0) { ExitsReached.Add((Side.Left, r)); return null; }
                if (nc >= _g.Width) { ExitsReached.Add((Side.Right, r)); return null; }
                if (BodyFree(nc, r) && !Deadly(nc, r)) return nc;
                return null;
            }

            S? DoDash(int c, int r, int dx, int dy)
            {
                int n = dx == 0 || dy == 0 ? DashLen : DashDiag;
                int steps = 0; bool inDream = false, refilled = false;
                while (true)
                {
                    int nc = c + dx, nr = r + dy;
                    if (nc < 0 || nc >= _g.Width)
                    {
                        if (inDream) return null;
                        ExitsReached.Add((nc < 0 ? Side.Left : Side.Right, r));
                        return null;
                    }
                    if (nr < 0 && Free(c, 0)) { ExitsReached.Add((Side.Top, c)); return null; }
                    if (nr >= _g.Height) { ExitsReached.Add((Side.Bottom, c)); return null; }
                    char a = At(nc, nr), b = At(nc, nr - 1);
                    if (a == '#' || b == '#')
                    {
                        if (inDream) return null;
                        break;
                    }
                    c = nc; r = nr; steps++;
                    refilled = refilled || Refill(c, r);
                    if (a == 'D' || b == 'D') { inDream = true; continue; }
                    if (inDream) break;
                    if (steps >= n) break;
                }
                if (Deadly(c, r) || !BodyFree(c, r)) return null;
                return new S(true, c, r, 0, PostDashSide, refilled, 0);
            }

            while (q.Count > 0)
            {
                var s = q.Dequeue();
                int c = s.C, r = s.R;
                if (!s.Air)
                {
                    if (At(c, r) == 'S') { Push(new S(true, c, r, SpringRise, SpringSide, true, 0)); continue; }
                    Ground.Add((c, r));
                    foreach (int dc in new[] { -1, 1 })
                    {
                        var nc = SideMove(c, r, dc);
                        if (nc == null) continue;
                        if (Standable(nc.Value, r)) Push(new S(false, nc.Value, r));
                        else Push(new S(true, nc.Value, r, 0, 0, true, 1));
                    }
                    Push(new S(true, c, r, JumpRise, JumpSide, true, 0));
                    Push(new S(true, c, r, 0, 0, true, 0));
                    continue;
                }

                int up = s.Up, side = s.Side, drift = s.Drift;
                bool hasDash = s.Dash;
                if (Deadly(c, r)) continue;
                if (!hasDash && Refill(c, r)) hasDash = true;
                if (up == 0 && At(c, r) == 'S') Push(new S(true, c, r, SpringRise, SpringSide, true, 0));
                if (Standable(c, r)) Push(new S(false, c, r));
                if (up > 0)
                {
                    if (r - 1 < 0) { if (r - 1 == -1 && Free(c, 0)) ExitsReached.Add((Side.Top, c)); }
                    else if (Free(c, r - 2) && Free(c, r - 1)) Push(new S(true, c, r - 1, up - 1, side, hasDash, 0));
                }
                if (side > 0 || drift > 0)
                {
                    foreach (int dc in new[] { -1, 1 })
                    {
                        var nc = SideMove(c, r, dc);
                        if (nc == null) continue;
                        if (side > 0) Push(new S(true, nc.Value, r, up, side - 1, hasDash, drift));
                        else Push(new S(true, nc.Value, r, 0, 0, hasDash, drift - 1));
                    }
                }
                if (!Floor(c, r))
                {
                    if (r + 1 >= _g.Height) ExitsReached.Add((Side.Bottom, c));
                    else if (Free(c, r + 1)) Push(new S(true, c, r + 1, 0, side, hasDash, 1));
                }
                if (hasDash && CanDash)
                    foreach (var (dx, dy) in Dirs8)
                    {
                        var res = DoDash(c, r, dx, dy);
                        if (res.HasValue) Push(res.Value);
                    }
            }
        }

        /// <summary>Start states for entering through an exit (the reach.py entry_states rules).</summary>
        public IEnumerable<(bool, int, int, int, int, bool, int)> EntryStates(Exit e)
        {
            switch (e.Side)
            {
                case Side.Left:
                case Side.Right:
                {
                    int c = e.Side == Side.Left ? 0 : _g.Width - 1;
                    for (int r = e.Offset; r < e.Offset + e.Size; r++)
                    {
                        if (Standable(c, r)) yield return (false, c, r, 0, 0, true, 0);
                        else if (BodyFree(c, r)) yield return (true, c, r, 0, 1, true, 1);
                    }
                    break;
                }
                case Side.Top:
                    for (int c = e.Offset; c < e.Offset + e.Size; c++)
                        if (Free(c, 0)) yield return (true, c, 0, 0, 1, true, 1);
                    break;
                default:
                    for (int c = e.Offset; c < e.Offset + e.Size; c++)
                        if (Free(c, _g.Height - 1)) yield return (true, c, _g.Height - 1, JumpRise, 3, false, 0);
                    break;
            }
        }

        public bool Reached(Exit e)
        {
            foreach (var (side, coord) in ExitsReached)
                if (side == e.Side && e.Contains(coord)) return true;
            return false;
        }
    }
}
