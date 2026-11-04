# My AI coding agents are hoarding 30 GB of disk

I run two AI coding agents on this machine: Devin CLI and T3 Code. Last week I went looking for why my disk felt heavier than it should, and found the answer sitting in two SQLite files.

Devin's session store, `~/.local/share/devin/cli/sessions.db`, weighs 16 GB, plus a 2.5 GB write-ahead log that hadn't been checkpointed. T3's `~/.t3/userdata/statev2.sqlite` is 10 GB. Call it 30 GB of agent session history, on one laptop, accumulated quietly in the background while I was doing other work.

## What's actually in there

I opened Devin's database read-only and looked around. It holds 9,022 sessions and 1,228,255 message rows. The per-table breakdown:

- `message_nodes`: 13.1 GB. Every prompt, reply, and tool call, stored verbatim.
- `tool_call_state`: 3.2 GB. Serialized tool state.
- `sessions`: 260 MB.
- The full-text search index: 115 MB.
- Everything else: noise.

That averages about 11 KB per message, which sounds insane until you remember what messages are in an agent session. Every `ls`, every file read, every command output gets saved whole. The agent that dumps 50 KB of build log into your terminal is also writing it to disk, permanently.

Here's the part that got me: the freelist is 11 pages out of 4.3 million. This is not a bloated database full of deleted rows waiting for a VACUUM. It's essentially 100% live data. The 16 GB is real content that nothing ever removes.

T3 is the same story with better manners. Its 10 GB is also all live data, freelist zero. It does checkpoint its WAL aggressively (21 MB outstanding versus Devin's 2.5 GB) and it cleans up the small stuff, worktrees, caches, scratch dirs. But the core store is an append-only event log plus projection tables, all in one file, with no retention either. Two independent teams shipped the same design: keep everything forever, in a single monolith, and make deletion the user's problem.

## Why it's like this

To be fair, some of this is the job. Resuming a session, searching past conversations, and handing context to subagents all need the full transcript on disk. SQLite plus WAL is a reasonable store for it.

But nothing about that requires keeping every byte forever, in one file, verbatim. A few boring changes would fix almost all of it:

- One database file per session, or one per time window. Deleting old history becomes `rm` on a file instead of a cascade delete inside a 16 GB live database.
- A retention sweep on startup. Anything older than N days that isn't pinned goes away.
- Truncate tool outputs after a while. The fact that a command ran and what it returned in summary is worth keeping. Its 50 KB of stdout from March is not.
- Actually checkpoint the WAL on a schedule, so the sidecar file doesn't quietly grow to gigabytes.

## The deletion trap

If you find your own copy of this problem and reach for `rm`, one detail matters: the file is open. Devin had three processes holding `sessions.db` when I checked. On Unix, deleting an open file unlinks the name but not the data. The processes keep writing to the inode, you get no space back, and the file keeps growing invisibly until every process exits. Quit the agent first, then delete. Ask me how I know this is the kind of detail that only bites you once.

Also worth knowing: this space is not "purgeable" in the macOS sense. Purgeable space is caches and APFS local snapshots the system can reclaim on demand. A SQLite database is a regular file. Finder will show it as plain used space forever.

## The kicker

The reason I have these exact numbers is that I asked my agent to go measure its own session store. It opened the database holding this very conversation, counted the rows, and reported back. Then it wrote that report into the same database, making the problem it described slightly worse.

There's a decent metaphor in there somewhere. I'll let it sit.
