using System;
using System.Text;

namespace CelPcg
{
    /// <summary>
    /// PCG32 (O'Neill, XSH RR 64/32). Same stream on every platform and .NET version,
    /// unlike System.Random, so a seed reproduces a room anywhere (editor, CLI, game).
    /// </summary>
    public sealed class Pcg32
    {
        private ulong _state;
        private readonly ulong _inc;

        public Pcg32(ulong seed, ulong stream = 54)
        {
            _inc = (stream << 1) | 1UL;
            _state = 0;
            NextUInt();
            _state += seed;
            NextUInt();
        }

        public uint NextUInt()
        {
            ulong old = _state;
            _state = unchecked(old * 6364136223846793005UL + _inc);
            uint xorshifted = (uint)(((old >> 18) ^ old) >> 27);
            int rot = (int)(old >> 59);
            return (xorshifted >> rot) | (xorshifted << ((-rot) & 31));
        }

        /// <summary>Uniform integer in [0, bound) without modulo bias.</summary>
        public int Next(int bound)
        {
            if (bound <= 0) throw new ArgumentOutOfRangeException(nameof(bound));
            uint b = (uint)bound;
            uint threshold = unchecked((uint)(-(int)b)) % b;
            while (true)
            {
                uint r = NextUInt();
                if (r >= threshold) return (int)(r % b);
            }
        }

        /// <summary>Uniform integer in [min, max] (inclusive).</summary>
        public int Range(int min, int max) => max <= min ? min : min + Next(max - min + 1);

        /// <summary>Uniform double in [0, 1).</summary>
        public double NextDouble() => NextUInt() / 4294967296.0;

        public bool Chance(double p) => NextDouble() < p;
    }

    /// <summary>Seed derivation: one world seed, stable per-room seeds.</summary>
    public static class Seed
    {
        public static ulong Fnv1a64(string text)
        {
            ulong h = 14695981039346656037UL;
            foreach (byte b in Encoding.UTF8.GetBytes(text))
            {
                h ^= b;
                h = unchecked(h * 1099511628211UL);
            }
            return h;
        }

        /// <summary>Derived seed for (seed, parts...), e.g. Derive(world, "CEL/wip/x", "room-1", "celpcg/0.1").</summary>
        public static ulong Derive(ulong seed, params object[] parts)
        {
            var sb = new StringBuilder(seed.ToString());
            foreach (var p in parts) sb.Append('|').Append(p);
            return Fnv1a64(sb.ToString());
        }
    }
}
