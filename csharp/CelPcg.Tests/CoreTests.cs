using System;
using System.Collections.Generic;
using System.Linq;
using CelPcg;
using Xunit;

public class RandomTests
{
    [Fact]
    public void Pcg32_is_deterministic_and_matches_reference()
    {
        // Reference: pcg32_srandom(42, 54) from the PCG C library demo -> first output 0xa15c02b7.
        var a = new Pcg32(42, 54);
        Assert.Equal(0xa15c02b7u, a.NextUInt());
        var b = new Pcg32(7); var c = new Pcg32(7);
        for (int i = 0; i < 100; i++) Assert.Equal(b.Next(1000), c.Next(1000));
    }

    [Fact]
    public void Derived_seeds_are_stable_and_distinct()
    {
        Assert.Equal(Seed.Derive(1, "a", 2), Seed.Derive(1, "a", 2));
        Assert.NotEqual(Seed.Derive(1, "a", 2), Seed.Derive(1, "a", 3));
        Assert.Equal(0xcbf29ce484222325UL, Seed.Fnv1a64(""));
    }
}

public class ReachTests
{
    // 16x10 room: floor at rows 8-9, a ledge `rise` tiles above the floor top, exit at the top right.
    static Room Step(int rise, bool dash)
    {
        var r = new Room(16, 10);
        r.Fill(0, 8, 16, 2, '#');
        r.Fill(0, 0, 1, 10, '#');
        r.Fill(15, 0, 1, 10, '#');
        r.Fill(10, 8 - rise, 6, rise, '#');
        r.Fill(15, 8 - rise - 3, 1, 3, '.');                  // opening on the right above the ledge
        r.Exits.Add(new Exit(Side.Left, 6, 2));
        r.Exits.Add(new Exit(Side.Right, 8 - rise - 3, 3));
        r.Fill(0, 6, 1, 2, '.');
        r.EntryIndex = 0; r.SpawnX = 2; r.SpawnY = 7;
        return r;
    }

    [Theory]
    [InlineData(3, false, true)]
    [InlineData(4, false, false)]
    [InlineData(4, true, true)]
    public void Jump_and_dash_limits(int rise, bool dash, bool reachable)
    {
        var room = Step(rise, dash);
        var reach = new Reach(room) { CanDash = dash };
        reach.Explore(new[] { (false, 2, 7, 0, 0, true, 0) });
        Assert.Equal(reachable, reach.Reached(room.Exits[1]));
    }

    [Fact]
    public void Spikes_kill_and_jumpthrough_is_a_floor()
    {
        var r = new Room(10, 8);
        r.Fill(0, 6, 10, 2, '#');
        r.Set(3, 5, '^');
        var reach = new Reach(r) { CanDash = false };
        Assert.False(reach.Standable(3, 5));
        r.Set(5, 3, '=');
        Assert.True(reach.Standable(5, 2));
    }
}

public class GeneratorTests
{
    static GenRequest Req(ulong seed, int d, params string[] exits) => new GenRequest
    {
        Seed = seed, Difficulty = d, Exits = exits.Select(Exit.Parse).ToList(),
    };

    [Fact]
    public void Same_request_same_room()
    {
        var a = PlatformChainGenerator.Generate(Req(42, 4, "left:16:4", "right:10:4", "top:20:4"));
        var b = PlatformChainGenerator.Generate(Req(42, 4, "left:16:4", "right:10:4", "top:20:4"));
        Assert.Equal(a.Room.Ascii(), b.Room.Ascii());
        Assert.Equal(a.AttemptSeed, b.AttemptSeed);
        var c = PlatformChainGenerator.Generate(Req(43, 4, "left:16:4", "right:10:4", "top:20:4"));
        Assert.NotEqual(a.Room.Ascii(), c.Room.Ascii());
    }

    public static IEnumerable<object[]> Layouts() => new[]
    {
        new object[] { new[] { "left:16:4", "right:16:4" } },
        new object[] { new[] { "left:16:4", "right:6:4" } },
        new object[] { new[] { "right:16:4", "left:5:4" } },
        new object[] { new[] { "left:16:4", "top:18:4" } },
        new object[] { new[] { "left:16:4", "right:10:4", "top:20:4" } },
        new object[] { new[] { "left:16:4", "bottom:20:4" } },
    };

    [Theory]
    [MemberData(nameof(Layouts))]
    public void Every_generated_room_is_valid_and_vanilla_only(string[] exits)
    {
        var vanilla = new HashSet<string> { "player", "spikesUp", "jumpThru", "spring", "refill", "dreamBlock" };
        for (int d = 1; d <= 10; d++)
            for (ulong seed = 1; seed <= 20; seed++)
            {
                var res = PlatformChainGenerator.Generate(Req(seed, d, exits));
                Assert.True(res.Report.Ok, string.Join("; ", res.Report.Issues));
                Assert.True(Validator.Check(res.Room).Ok);
                var ents = res.Room.Entities();
                Assert.Single(ents, e => e.Name == "player");
                Assert.All(ents, e => Assert.Contains(e.Name, vanilla));
                Assert.All(ents, e => Assert.InRange(e.X, 0, res.Room.Width * 8));
                Assert.All(res.Room.FgRows(), row => Assert.Equal(res.Room.Width, row.Length));
            }
    }

    [Fact]
    public void Validator_reports_a_blocked_exit()
    {
        var res = PlatformChainGenerator.Generate(Req(5, 3, "left:16:4", "right:16:4"));
        var room = res.Room;
        room.Fill(room.Width - 2, 0, 2, room.Height, '#');
        var rep = Validator.Check(room);
        Assert.False(rep.Ok);
        Assert.Contains(rep.Issues, i => i.Contains("blocked"));
    }

    [Fact]
    public void Entry_must_be_horizontal()
    {
        Assert.Throws<ArgumentException>(() => PlatformChainGenerator.Generate(Req(1, 3, "top:10:4", "right:16:4")));
    }
}

public class LayoutQualityTests
{
    [Fact]
    public void Refills_are_never_embedded_in_platforms()
    {
        for (int d = 5; d <= 10; d++)
            for (ulong seed = 1; seed <= 40; seed++)
            {
                var req = new GenRequest { Seed = seed, Difficulty = d,
                    Exits = new[] { "left:16:4", "right:10:4", "top:20:4" }.Select(Exit.Parse).ToList() };
                var room = PlatformChainGenerator.Generate(req).Room;
                for (int y = 0; y < room.Height; y++)
                    for (int x = 1; x < room.Width - 1; x++)
                        if (room.At(x, y) == '*')
                            Assert.False(room.At(x - 1, y) is '#' or '=' && room.At(x + 1, y) is '#' or '=',
                                $"refill at ({x},{y}) sits inside a platform, seed {seed} d{d}\n{room.Ascii()}");
            }
    }
}
