using System.Collections.Generic;
using System.Linq;

namespace CelPcg
{
    public sealed class ValidationReport
    {
        public readonly List<string> Issues = new List<string>();
        public readonly List<string> ExitsReached = new List<string>();
        public int GroundCells;
        public bool Ok => Issues.Count == 0;
    }

    /// <summary>
    /// Tier 0 (structure) and tier 1 (reachability) checks. The same checks run in every host:
    /// the CLI before a room is written, and the mod before a generated room is used.
    /// </summary>
    public static class Validator
    {
        public static ValidationReport Check(Room room)
        {
            var rep = new ValidationReport();
            // Tier 0: structure.
            if (room.Exits.Count == 0) rep.Issues.Add("room has no exits");
            if (room.EntryIndex < 0 || room.EntryIndex >= room.Exits.Count) rep.Issues.Add("entry index is not an exit");
            foreach (var e in room.Exits)
            {
                bool vertical = e.Side == Side.Left || e.Side == Side.Right;
                int limit = vertical ? room.Height : room.Width;
                if (e.Size < (vertical ? 2 : 1) || e.Offset < 0 || e.Offset + e.Size > limit)
                    rep.Issues.Add($"exit {e} does not fit the {room.Width}x{room.Height} room");
                else if (!ExitOpen(room, e))
                    rep.Issues.Add($"exit {e} is blocked at the border");
            }
            var reach = new Reach(room);
            if (!reach.Standable(room.SpawnX, room.SpawnY))
                rep.Issues.Add($"player spawn ({room.SpawnX},{room.SpawnY}) is not a safe standing spot");
            if (!rep.Ok) return rep;

            // Tier 1: from the entry, every other exit must be reachable.
            var starts = reach.EntryStates(room.Entry).ToList();
            starts.Add((false, room.SpawnX, room.SpawnY, 0, 0, true, 0));
            reach.Explore(starts);
            rep.GroundCells = reach.Ground.Count;
            for (int i = 0; i < room.Exits.Count; i++)
            {
                if (i == room.EntryIndex) continue;
                var e = room.Exits[i];
                if (reach.Reached(e)) rep.ExitsReached.Add(e.ToString());
                else rep.Issues.Add($"exit {e} is not reachable from the entry {room.Entry} (no climbing or wall jumps assumed)");
            }
            if (!reach.Ground.Contains((room.SpawnX, room.SpawnY)))
                rep.Issues.Add("player spawn is not reachable from the entry");
            return rep;
        }

        private static bool ExitOpen(Room room, Exit e)
        {
            for (int k = e.Offset; k < e.Offset + e.Size; k++)
            {
                char c = e.Side switch
                {
                    Side.Left => room.At(0, k),
                    Side.Right => room.At(room.Width - 1, k),
                    Side.Top => room.At(k, 0),
                    _ => room.At(k, room.Height - 1),
                };
                if (c == '#') return false;
            }
            return true;
        }
    }
}
