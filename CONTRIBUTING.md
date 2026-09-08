# Contributing

The whole app is one file, [`app.py`](app.py), with no dependencies beyond the
Python standard library. Please keep it that way — the point of this project is
that it runs anywhere Python does and ships as a single binary.

## Layout of `app.py`

| Section | What lives there |
|---|---|
| `log`, `pick_folder`, `show_error` | OS integration: native dialogs, console-safe output |
| `find_session_files`, `parse_session` | Reading transcripts off disk |
| `Scanner` | Caching, listing, deleting |
| `PAGE` | The entire UI — HTML, CSS and JS in one string |
| `make_handler`, `run_gui`, `run_list` | HTTP server and entry points |

## Running and testing

    python3 app.py --list        # exercises the scanner without a browser
    python3 app.py --root DIR    # point at a throwaway folder

Never test deletion against a real `~/.claude/projects`. Make a folder of dummy
`.jsonl` files instead — one JSON object per line, with `type` set to
`ai-title`, `user` or `assistant`.

## Pull requests

Match the surrounding style: comments explain *why*, not *what*. If you touch
deletion, keep both guard rails — the token check and the "must be in the last
scan" rule.
