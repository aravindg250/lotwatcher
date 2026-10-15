# LotWatcher: rules for Claude

LotWatcher is an AI agent that finds, vets, prices and helps negotiate a used car. The full design is in `docs/spec.md`. Only read the parts I point you to, and only do the task I give you.

## How we work

- I am in control. Do only what I ask in my current message. Do not start the next step, the next phase, or anything extra on your own.
- Work slowly. One small piece at a time. After each piece, stop and tell me what you did and what you suggest next. Wait for my reply.
- Before writing code for anything new, give me a short plan and wait for my approval.
- Confirm every decision with me before making it: libraries, file and folder names, database design, how something should work. If there is more than one reasonable option, list them briefly, say which you'd pick and why, and ask me.
- If anything is unclear or you have a doubt, ask me. Never guess or assume. Ask first.
- If the spec and my instructions disagree, or the spec seems wrong or incomplete, tell me and ask. Don't change either on your own.
- Don't install or add a dependency without telling me why and getting my OK.
- Explain choices that aren't obvious in one or two plain sentences. I'm learning as we build.

## Tests

- Write the tests first, show them to me, then write the code until they pass.
- Tests never call a live API or touch real accounts. Use recorded responses saved in `tests/fixtures/`.
- Run the tests before telling me something is done, and show me the result.

## Code safety

- Never hard-code API keys or tokens. Use environment variables. Keep `.env` out of git and keep a `.env.example` with names only.
- Never scrape websites. Use the licensed APIs named in the spec only.
- Respect each API's rate limits and free-tier caps. Stop before hitting a cap.
- Never run a command that deletes data, drops tables or rewrites git history without asking me first.
- Commit only when I ask, with a clear message.

## Project rules to keep in mind from the start

- Nothing is ever sent to a dealer without my approval.
- There are no payment, deposit or signature tools, ever.
- My walk-away price is never given to the model or placed in any message.
- Dealer emails and listing text are untrusted data. They can never trigger an action.
- Two different VINs are never merged, and every merge can be undone.