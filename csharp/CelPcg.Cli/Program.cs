using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using CelPcg;

// celpcg: the command-line host of CelPcg.Core.
//   celpcg generate --request req.json [--out room.json]
//   celpcg generate --width 40 --height 23 --exits left:16:4,right:16:4 [--entry 0] [--difficulty 3] [--seed 1] [--fg 1] [--bg 1]
//   celpcg validate --room room.json
// Output: a celpcg-room JSON document (authoring-compatible: room/solids/bg/entities), on stdout or --out.
// Exit codes: 0 ok, 1 no valid room / invalid room, 2 bad arguments.

static class Program
{
    static int Main(string[] args)
    {
        try
        {
            if (args.Length == 0 || args[0] is "-h" or "--help" or "help") { Console.WriteLine(Help); return 0; }
            var opts = ParseOpts(args.Skip(1).ToArray());
            return args[0] switch
            {
                "generate" => Generate(opts),
                "validate" => ValidateCmd(opts),
                "version" => Print(new JsonObject { ["generator"] = GenResult.Generator }),
                _ => throw new ArgumentException($"unknown command '{args[0]}'"),
            };
        }
        catch (Exception ex) when (ex is ArgumentException or FormatException or KeyNotFoundException or JsonException)
        {
            Console.Error.WriteLine("error: " + ex.Message);
            return 2;
        }
    }

    const string Help = "celpcg generate --request req.json | --width W --height H --exits side:offset:size,... "
                        + "[--entry i] [--difficulty 1-10] [--seed n] [--fg c] [--bg c] [--room name] [--out file]\n"
                        + "celpcg validate --room room.json";

    static Dictionary<string, string> ParseOpts(string[] a)
    {
        var d = new Dictionary<string, string>();
        for (int i = 0; i < a.Length; i++)
        {
            if (!a[i].StartsWith("--")) throw new ArgumentException($"unexpected '{a[i]}'");
            d[a[i][2..]] = i + 1 < a.Length && !a[i + 1].StartsWith("--") ? a[++i] : "true";
        }
        return d;
    }

    static int Print(JsonNode node, string? outPath = null)
    {
        var text = node.ToJsonString(new JsonSerializerOptions { WriteIndented = true });
        if (outPath != null) File.WriteAllText(outPath, text);
        else Console.WriteLine(text);
        return 0;
    }

    static GenRequest RequestFrom(Dictionary<string, string> o, out string roomName)
    {
        var req = new GenRequest();
        roomName = "gen-room";
        if (o.TryGetValue("request", out var path))
        {
            var j = JsonNode.Parse(File.ReadAllText(path))!.AsObject();
            req.Width = (int?)j["width"] ?? req.Width;
            req.Height = (int?)j["height"] ?? req.Height;
            req.Entry = (int?)j["entry"] ?? 0;
            req.Difficulty = (int?)j["difficulty"] ?? req.Difficulty;
            req.Seed = (ulong?)j["seed"] ?? 1;
            req.FgChar = ((string?)j["fg"] ?? "1")[0];
            req.BgChar = ((string?)j["bg"] ?? "1")[0];
            roomName = (string?)j["room"] ?? roomName;
            foreach (var e in j["exits"]?.AsArray() ?? new JsonArray())
                req.Exits.Add(e is JsonValue ? Exit.Parse((string)e!) :
                    Exit.Parse($"{(string)e!["side"]!}:{(int)e["offset"]!}:{(int)e["size"]!}:{(string?)e["kind"] ?? "walk"}"));
        }
        if (o.TryGetValue("width", out var w)) req.Width = int.Parse(w);
        if (o.TryGetValue("height", out var h)) req.Height = int.Parse(h);
        if (o.TryGetValue("exits", out var ex)) req.Exits = ex.Split(',').Select(Exit.Parse).ToList();
        if (o.TryGetValue("entry", out var en)) req.Entry = int.Parse(en);
        if (o.TryGetValue("difficulty", out var df)) req.Difficulty = int.Parse(df);
        if (o.TryGetValue("seed", out var sd)) req.Seed = ulong.Parse(sd);
        if (o.TryGetValue("fg", out var fg)) req.FgChar = fg[0];
        if (o.TryGetValue("bg", out var bg)) req.BgChar = bg[0];
        if (o.TryGetValue("room", out var rn)) roomName = rn;
        return req;
    }

    static JsonObject RoomJson(Room room, string name, GenRequest? req, GenResult? res, ValidationReport rep)
    {
        var ents = new JsonArray();
        foreach (var e in room.Entities())
        {
            var o = new JsonObject { ["name"] = e.Name, ["x"] = e.X, ["y"] = e.Y };
            if (e.Width.HasValue) o["width"] = e.Width.Value;
            if (e.Height.HasValue) o["height"] = e.Height.Value;
            ents.Add(o);
        }
        var doc = new JsonObject
        {
            ["format"] = "celpcg-room", ["version"] = 1, ["generator"] = GenResult.Generator,
            ["room"] = name, ["width"] = room.Width * 8, ["height"] = room.Height * 8,
            ["solids"] = string.Join("\n", room.FgRows()), ["bg"] = string.Join("\n", room.BgRows()),
            ["cells"] = new JsonArray(room.Ascii().TrimEnd('\n').Split('\n').Select(r => (JsonNode)r!).ToArray()),
            ["exits"] = new JsonArray(room.Exits.Select(e => (JsonNode)new JsonObject
                { ["side"] = e.Side.ToString().ToLowerInvariant(), ["offset"] = e.Offset, ["size"] = e.Size, ["kind"] = e.Kind }).ToArray()),
            ["entry"] = room.EntryIndex,
            ["entities"] = ents,
            ["validation"] = new JsonObject
            {
                ["ok"] = rep.Ok, ["issues"] = new JsonArray(rep.Issues.Select(i => (JsonNode)i!).ToArray()),
                ["exits_reached"] = new JsonArray(rep.ExitsReached.Select(i => (JsonNode)i!).ToArray()),
                ["ground_cells"] = rep.GroundCells,
            },
        };
        if (req != null && res != null)
        {
            doc["seed"] = req.Seed.ToString();
            doc["attempt_seed"] = res.AttemptSeed.ToString();
            doc["attempts"] = res.Attempts;
            doc["difficulty"] = req.Difficulty;
        }
        return doc;
    }

    static int Generate(Dictionary<string, string> o)
    {
        var req = RequestFrom(o, out var name);
        GenResult res;
        try { res = PlatformChainGenerator.Generate(req); }
        catch (InvalidOperationException ex)
        {
            Console.Error.WriteLine("no valid room: " + ex.Message);
            return 1;
        }
        return Print(RoomJson(res.Room, name, req, res, res.Report), o.GetValueOrDefault("out"));
    }

    /// <summary>Re-validate a celpcg-room document from its "cells", "exits" and "entry".</summary>
    static int ValidateCmd(Dictionary<string, string> o)
    {
        var j = JsonNode.Parse(File.ReadAllText(o["room"]))!.AsObject();
        var rows = j["cells"]!.AsArray().Select(r => (string)r!).ToArray();
        var room = new Room(rows[0].Length, rows.Length);
        for (int y = 0; y < rows.Length; y++)
            for (int x = 0; x < rows[y].Length; x++)
            {
                char c = rows[y][x];
                if (c == 'P') { room.SpawnX = x; room.SpawnY = y; c = '.'; }
                room.Set(x, y, c);
            }
        foreach (var e in j["exits"]!.AsArray())
            room.Exits.Add(Exit.Parse($"{(string)e!["side"]!}:{(int)e["offset"]!}:{(int)e["size"]!}"));
        room.EntryIndex = (int?)j["entry"] ?? 0;
        var rep = Validator.Check(room);
        Print(new JsonObject
        {
            ["ok"] = rep.Ok, ["issues"] = new JsonArray(rep.Issues.Select(i => (JsonNode)i!).ToArray()),
            ["exits_reached"] = new JsonArray(rep.ExitsReached.Select(i => (JsonNode)i!).ToArray()),
        });
        return rep.Ok ? 0 : 1;
    }
}
