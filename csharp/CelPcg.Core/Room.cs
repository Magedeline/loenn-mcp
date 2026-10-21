using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;

namespace CelPcg
{
    public enum Side { Left, Right, Top, Bottom }

    /// <summary>An opening in the room border. Offset/Size are in tiles: rows for Left/Right, columns for Top/Bottom.</summary>
    public sealed class Exit
    {
        public Side Side;
        public int Offset;
        public int Size;
        public string Kind = "walk";

        public Exit(Side side, int offset, int size, string kind = "walk")
        {
            Side = side; Offset = offset; Size = size; Kind = kind;
        }

        public bool Contains(int coord) => coord >= Offset && coord < Offset + Size;

        public override string ToString() => $"{Side.ToString().ToLowerInvariant()}:{Offset}:{Size}";

        public static Exit Parse(string spec)
        {
            var p = spec.Split(':');
            if (p.Length < 3) throw new FormatException($"exit '{spec}': use side:offset:size (side = left/right/top/bottom)");
            Side side = p[0].ToLowerInvariant() switch
            {
                "left" or "l" => Side.Left,
                "right" or "r" => Side.Right,
                "top" or "up" or "u" => Side.Top,
                "bottom" or "down" or "d" => Side.Bottom,
                _ => throw new FormatException($"exit '{spec}': unknown side '{p[0]}'"),
            };
            return new Exit(side, int.Parse(p[1]), int.Parse(p[2]), p.Length > 3 ? p[3] : "walk");
        }
    }

    /// <summary>A Celeste entity in room pixel coordinates.</summary>
    public sealed class EntityPlacement
    {
        public string Name;
        public int X, Y;
        public int? Width, Height;

        public EntityPlacement(string name, int x, int y, int? width = null, int? height = null)
        {
            Name = name; X = x; Y = y; Width = width; Height = height;
        }
    }

    /// <summary>
    /// The room intermediate representation. Cells hold gameplay symbols used by the
    /// generator and the reach model:
    ///   '#' solid, '.' air, '^' floor spikes (on the air cell above a floor),
    ///   '=' jump-through (passable from below, a floor from above), 'S' spring (on the floor),
    ///   '*' refill, 'D' dream block.
    /// Fg/Bg rows (Celeste tileset characters) and entities are derived from the cells.
    /// </summary>
    public sealed class Room
    {
        public readonly int Width, Height;               // tiles
        public readonly char[,] Cells;                    // [x, y]
        public readonly List<Exit> Exits = new List<Exit>();
        public int EntryIndex;
        public int SpawnX, SpawnY;                        // feet tile of the player spawn
        public char FgChar = '1', BgChar = '1';

        public Room(int width, int height)
        {
            if (width < 8 || height < 8) throw new ArgumentException("a room needs at least 8x8 tiles");
            Width = width; Height = height;
            Cells = new char[width, height];
            Fill(0, 0, width, height, '.');
        }

        public char At(int x, int y) => x < 0 || y < 0 || x >= Width || y >= Height ? '#' : Cells[x, y];

        public void Set(int x, int y, char c)
        {
            if (x >= 0 && y >= 0 && x < Width && y < Height) Cells[x, y] = c;
        }

        public void Fill(int x, int y, int w, int h, char c)
        {
            for (int j = y; j < y + h; j++)
                for (int i = x; i < x + w; i++)
                    Set(i, j, c);
        }

        public Exit Entry => Exits[EntryIndex];

        public string[] FgRows()
        {
            var rows = new string[Height];
            for (int y = 0; y < Height; y++)
            {
                var sb = new StringBuilder(Width);
                for (int x = 0; x < Width; x++) sb.Append(Cells[x, y] == '#' ? FgChar : '0');
                rows[y] = sb.ToString();
            }
            return rows;
        }

        /// <summary>Background wall behind every non-solid interior cell.</summary>
        public string[] BgRows()
        {
            var rows = new string[Height];
            for (int y = 0; y < Height; y++)
            {
                var sb = new StringBuilder(Width);
                for (int x = 0; x < Width; x++) sb.Append(Cells[x, y] == '#' ? '0' : BgChar);
                rows[y] = sb.ToString();
            }
            return rows;
        }

        /// <summary>Entities for the symbols, with Celeste anchors; runs merge into one entity.</summary>
        public List<EntityPlacement> Entities()
        {
            var list = new List<EntityPlacement>
            {
                new EntityPlacement("player", SpawnX * 8 + 4, (SpawnY + 1) * 8),
            };
            for (int y = 0; y < Height; y++)
            {
                int x = 0;
                while (x < Width)
                {
                    char c = Cells[x, y];
                    int run = 1;
                    while (x + run < Width && Cells[x + run, y] == c) run++;
                    switch (c)
                    {
                        case '^': list.Add(new EntityPlacement("spikesUp", x * 8, (y + 1) * 8, width: run * 8)); break;
                        case '=': list.Add(new EntityPlacement("jumpThru", x * 8, y * 8, width: run * 8)); break;
                        case 'S': for (int i = 0; i < run; i++) list.Add(new EntityPlacement("spring", (x + i) * 8 + 4, (y + 1) * 8)); break;
                        case '*': for (int i = 0; i < run; i++) list.Add(new EntityPlacement("refill", (x + i) * 8 + 4, y * 8 + 4)); break;
                    }
                    x += run;
                }
            }
            // Dream blocks: greedy rectangles.
            var done = new bool[Width, Height];
            for (int y = 0; y < Height; y++)
                for (int x = 0; x < Width; x++)
                {
                    if (Cells[x, y] != 'D' || done[x, y]) continue;
                    int w = 0;
                    while (x + w < Width && Cells[x + w, y] == 'D' && !done[x + w, y]) w++;
                    int h = 1;
                    while (y + h < Height && Enumerable.Range(x, w).All(i => Cells[i, y + h] == 'D' && !done[i, y + h])) h++;
                    for (int j = y; j < y + h; j++) for (int i = x; i < x + w; i++) done[i, j] = true;
                    list.Add(new EntityPlacement("dreamBlock", x * 8, y * 8, w * 8, h * 8));
                }
            return list;
        }

        public string Ascii()
        {
            var sb = new StringBuilder();
            for (int y = 0; y < Height; y++)
            {
                for (int x = 0; x < Width; x++)
                    sb.Append(x == SpawnX && y == SpawnY ? 'P' : Cells[x, y]);
                sb.Append('\n');
            }
            return sb.ToString();
        }
    }
}
