# Gmail digest

A small tool that reads one Gmail inbox over IMAP and saves the last 24 hours of email as compact, sanitised reports that an AI agent can read, prioritise and reply from. It is built for Hermes Agent on Linux but runs anywhere Python 3.9 or later does. Standard library only.

Each run takes an inbox nickname, a Gmail address, an app password and a period (24 hours unless told otherwise), and writes:

```
report-<nickname>-inbox-<YYYY-MM-DD>.md        emails, trimmed, with factual signals
report-<nickname>-inbox-<YYYY-MM-DD>.json      the same, as data
report-<nickname>-newsletter-<YYYY-MM-DD>.md   newsletters and mailing lists, trimmed
report-<nickname>-newsletter-<YYYY-MM-DD>.json
```

The date is the machine's local date when the run happens. A second run on the same day replaces that day's reports.

## Setting up a mailbox

Gmail only accepts IMAP logins from scripts with an app password. Switch on 2-Step Verification for the Google account, then create an app password at myaccount.google.com/apppasswords. Google shows it as four groups of four letters; the spaces don't matter. Workspace accounts need the administrator to allow app passwords.

Keep each password out of the command line, where it would show up in shell history and process lists. Either put it in an environment variable, or in a file only you can read:

```
mkdir -p ~/.config/gmail-digest && chmod 700 ~/.config/gmail-digest
printf '%s' 'abcd efgh ijkl mnop' > ~/.config/gmail-digest/personal.pass
chmod 600 ~/.config/gmail-digest/personal.pass
```

## Running it

```
python3 gmail_digest.py --nickname personal --user you@gmail.com \
    --password-file ~/.config/gmail-digest/personal.pass

GMAIL_APP_PASSWORD='abcd efgh ijkl mnop' \
    python3 gmail_digest.py --nickname personal --user you@gmail.com --hours 48
```

For several mailboxes, run once per mailbox with its own nickname and password. `--password-env WORK_APP_PASSWORD` reads from a differently named variable, and `--password-stdin` reads the password from a pipe.

The script prints a short JSON summary when it finishes, including the paths of the files it wrote, so an agent knows what to open next. It exits with 0 on success and 1 if it couldn't log in or read the inbox. A failed run still writes an inbox report explaining what went wrong.

Reports go to `~/mail_digests` unless `--output-dir` says otherwise. Files are created readable by the owner only.

To run every night just after midnight, add a line like this with `crontab -e`:

```
5 0 * * * /usr/bin/python3 /path/to/gmail_digest.py --nickname personal --user you@gmail.com --password-file /home/you/.config/gmail-digest/personal.pass >> /home/you/mail_digests/cron.log 2>&1
```

## What ends up in the reports

Only the inbox is read. Gmail keeps Spam and Bin as separate folders, so anything Gmail has classed as spam never appears. Emails in the Promotions, Social, Updates and Forums tabs are still in the inbox; `--primary-only` leaves them out.

Each email keeps its sender, To and Cc addresses, subject, received time, attachment names and a trimmed body. The body is the text Gmail would display: the HTML version is preferred to the plain-text alternative, because the plain-text part can carry text the recipient never saw. Quoted earlier messages, signatures, "Sent from my iPhone" lines and legal disclaimers are removed, every link is shortened to its domain, and the body is cut at 2,000 characters (`--max-chars`). Forwarded messages are kept, since the forwarded text is usually the point. Body text across the whole inbox report is capped at 60,000 characters (`--max-total-chars`); emails past the cap are still listed, without their bodies, and the report says so.

Each email also carries factual signals and nothing that ranks it, so the reading model decides what matters:

| Signal | Where it comes from |
|---|---|
| Addressed to you directly, in Cc only, or neither (Bcc or a list) | To and Cc headers, compared with `--user` and any `--alias` |
| Reply to your email | Its In-Reply-To or References headers match a message in your Sent mail |
| Sender known, and why | Listed in `--known-senders`, or you have emailed them in the last 90 days |
| Sent by you | The From address is yours |
| SPF, DKIM and DMARC results | The authentication header Gmail adds on arrival |
| Unread, starred, marked important by Gmail | Gmail's own flags and labels |
| Bulk or mailing list, automated | List-Unsubscribe, List-Id, Precedence and Auto-Submitted headers |
| Number of other recipients | To and Cc |

The two Sent-mail signals come from reading the headers of the last 90 days of Sent mail, never the content. `--no-sent-check` switches that off.

The known-senders file holds one address or `@domain` per line, with `#` for comments. For Gmail addresses, dots and `+tags` are ignored, as Gmail ignores them.

Newsletters and mailing-list emails go to the separate newsletter report by default, trimmed to 1,000 characters each. `--newsletters list` lists them as one line each in the inbox report instead, and `--newsletters exclude` only counts them.

## Protection against prompt injection

No filter can reliably spot a prompt injection, because an injection is ordinary language. This tool removes the usual hiding places and labels everything as untrusted. The real protection is that the agent reading the reports can't send, delete, pay or share anything without a person approving it. Treat that as a requirement of any setup that uses this tool.

What the tool does:

The mailbox is opened read-only and emails are fetched with PEEK, so nothing is marked as read, moved or deleted. Attachments are never opened or saved; only their names are listed. Only the first 1 MB of each email is downloaded.

Text a person couldn't see is removed: elements hidden with CSS (`display:none`, zero size, zero opacity and similar), text coloured the same as its background, HTML comments, scripts and styles. Zero-width and text-direction control characters are stripped from bodies, subjects and names. Subjects and names are capped at 200 characters.

Bodies are checked, after cleaning, for phrasing typical of text written to steer an AI: "ignore previous instructions", text addressed to an AI assistant, requests to hide something from the user, model control tokens, and requests to forward all emails or reveal passwords. The check also covers the subject and any hidden text that was removed. A match withholds the body: the report lists the sender, the matched phrase type and the subject (unless the subject itself matched). False positives happen, for example with newsletters about AI security, so `--include-flagged-bodies` includes these bodies with a warning note.

Every body sits between START and END markers that carry a random code generated for that run, so an email can't fake the end of its own body. The top of each report tells the reading model to treat marked text as data and never follow instructions inside it.

Known limits: the colour check compares exact colour values, so near-white text on a white background gets through. Injection phrased in other ways, or in other languages, won't match the patterns. Display names can be forged, which is why the authentication results are included.

## Tests

```
python3 -m unittest discover -s tools/gmail_digest/tests -v
```

The tests run against a fake Gmail server built from saved emails. They cover an empty period, a personal reply and its signals, Cc and unknown senders, the known-senders file, each newsletter mode, hidden and visible injection, harmless hidden preview text, white text on a dark background, long threads, quoted history in HTML, forwarded mail, attachments, unusual and unknown character encodings, the boundary of the time window, login failure, a missing password, a missing Sent folder, the mailbox being left unchanged, the total size cap, the body markers and file permissions.

They don't prove it works against your real inbox. The last check is a run against a real mailbox, done by the mailbox's owner.

## Using it from Hermes Agent

`SKILL.md` in this folder is a skill file. Copy the folder into your Hermes skills directory (by default `~/.hermes/skills/`) so the agent knows when and how to run the tool and how to treat what it reads.
