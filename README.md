> [!IMPORTANT]
> **This is a fork of [wizarrrr/wizarr](https://github.com/wizarrrr/wizarr).**
> Everything below the line at the end of this block is upstream's own README, unchanged.
> If you are not me, you almost certainly want [the upstream image](https://github.com/wizarrrr/wizarr).
> This fork exists to run one household's invites, for friends and family on Plex, Audiobookshelf, Navidrome and a book library. Its images are amd64-only.

## What this fork adds

Wizarr's invite flow was built around one person joining one media server. This fork stretches it to a person joining several services from one invite, each with its own account, and to the person on the admin side who has to keep track of who has what.

The Origin column says who wrote a change and where it stands upstream: **merged**, **open** (the PR is linked), **not sent yet** (an upstream candidate) or **fork only** (outside upstream's scope, so it is never sent).

### Invites

| Change | Origin |
|---|---|
| An invite for several services is a checklist. Plex is one step and every other service shares one account step, each can be done or skipped, and the link picks up where it left off on another device | This fork. Fork only |
| Shared invites: each person proves their email with a code, and each person has their own progress and their own row on the invite card | This fork. Fork only |
| A service the person already has counts as done on the account step, so adding one more piece to someone who has two does not get stuck. Email matches ignore case | This fork. Fork only |
| Create Invitation as four steps (Who, How long, What they get, Review), with Multi-use and Supporter options and a link expiry that defaults to a week | This fork. Fork only |
| "Who is this for" on an invite, kept with who created it | This fork. Fork only |
| "Access ends on" date on an invite | This fork. Fork only |
| Advice on the invite form for combinations that do not work (Plex mixed with services that skip the password step) | This fork. Fork only |
| Server notes and links shown to the person once they are set up | This fork. Fork only |
| Share button beside Copy on a new invite | This fork. Not sent yet |
| A Plex and Audiobookshelf invite gives the Audiobookshelf account the invite's libraries | This fork. Not sent yet |
| The join page shows posters only from the invite's own servers | This fork. Not sent yet |
| Settings > Email: an SMTP server for the mail Wizarr sends, used for the codes above | This fork. Not sent yet |

### Users and expiry

| Change | Origin |
|---|---|
| A user whose expiry disable failed is kept instead of deleted | This fork, merged upstream as [#1478](https://github.com/wizarrrr/wizarr/pull/1478) |
| A saved future expiry switches a disabled account back on, and Extend everyone does the same for everyone it extends | This fork. Not sent yet |
| Switch off and Switch on on the Users card, with a spinner while it runs, and a switched-off account's service pills struck through | This fork. Fork only |
| A failed join shows as failed on both the invite card and the Users card | This fork. Fork only |
| Deleting a user from one server keeps the row when that server did not confirm the delete | This fork, open upstream as [#1488](https://github.com/wizarrrr/wizarr/pull/1488) (issue [#1487](https://github.com/wizarrrr/wizarr/issues/1487)) |
| Addresses on a protected list (Settings > General) are skipped by expiry and by invite redemption | This fork. Fork only |
| Library ticks: libraries found by a scan start unticked, and a Navidrome server's first scan ticks what Navidrome offers to new users | This fork. Fork only |

### Servers

| Change | Origin |
|---|---|
| Navidrome: Wizarr talks to Navidrome's own API, takes the libraries from the invite and uses one username and password form (shared with RomM) | This fork. Fork only |
| A `provisioning_hook` server type: Wizarr calls a small executor you run to grant, disable and remove access on a service Wizarr has no client for. The household uses it for a book library | This fork. Fork only |
| Each client declares what it needs (sign-in kind, form fields, password strength), and the join code reads that instead of checking server types | This fork, open upstream as [#1490](https://github.com/wizarrrr/wizarr/pull/1490) |
| Audiobookshelf accounts are made with an explicit list of libraries, never "all" by default | This fork. Fork only |
| Add Server: the scan lists only the scanned server, credentials stay in the form after a failed test, the URL is trimmed | This fork. Fork only |

### Admin and accounts

| Change | Origin |
|---|---|
| Cloudflare Access signs in as your own admin account, with a Name on admin accounts, and a read-only Access status card on Settings > Admins | This fork. Fork only |
| An audit log: one INFO line per account change, with who did it | This fork, open upstream as [#1489](https://github.com/wizarrrr/wizarr/pull/1489) |
| Enter on Settings > General saves instead of opening Create API Key | This fork, open upstream as [#1486](https://github.com/wizarrrr/wizarr/pull/1486) (issue [#1485](https://github.com/wizarrrr/wizarr/issues/1485)) |
| `RATELIMIT_ENABLED`: limits per visitor, using the client address from upstream's `TRUST_PROXY_HEADERS` when behind a proxy | This fork. Not sent yet |

### Build

| Change | Origin |
|---|---|
| Fork-only CI: amd64 images tagged `sha-<7>`, no `latest` tag | This fork. Fork only |

## Upstream first

Anything here that is not specific to this household belongs upstream, and goes there as its own PR. Thirteen have merged so far:

- [#1432](https://github.com/wizarrrr/wizarr/pull/1432) the server-type picker comes from the client registry
- [#1433](https://github.com/wizarrrr/wizarr/pull/1433) stored UTC timestamps show in local time
- [#1438](https://github.com/wizarrrr/wizarr/pull/1438) wizard templates keep their presentational markup
- [#1439](https://github.com/wizarrrr/wizarr/pull/1439) each media client owns its connection check
- [#1441](https://github.com/wizarrrr/wizarr/pull/1441) expiry history is reused when an expired user is re-enabled
- [#1442](https://github.com/wizarrrr/wizarr/pull/1442) choose which server the join page posters come from
- [#1443](https://github.com/wizarrrr/wizarr/pull/1443) verify the Cloudflare Access token for admin requests
- [#1445](https://github.com/wizarrrr/wizarr/pull/1445) a nickname for users with a single account
- [#1447](https://github.com/wizarrrr/wizarr/pull/1447) every person who joins an unlimited invite is recorded
- [#1449](https://github.com/wizarrrr/wizarr/pull/1449) one email pattern for every server type
- [#1451](https://github.com/wizarrrr/wizarr/pull/1451) an expired user is kept when the media server delete fails
- [#1453](https://github.com/wizarrrr/wizarr/pull/1453) an Emby user is not left without a password
- [#1478](https://github.com/wizarrrr/wizarr/pull/1478) a user whose expiry disable failed is kept

Open: [#1486](https://github.com/wizarrrr/wizarr/pull/1486), [#1488](https://github.com/wizarrrr/wizarr/pull/1488), [#1489](https://github.com/wizarrrr/wizarr/pull/1489) and [#1490](https://github.com/wizarrrr/wizarr/pull/1490), described above. Issue [#1480](https://github.com/wizarrrr/wizarr/issues/1480) (a deleted user leaves its empty Identity row behind) is open with no PR. [#1426](https://github.com/wizarrrr/wizarr/pull/1426) was closed, because upstream removed the panel it fixed.

What stays in the fork is the invite checklist and everything built on it, the Navidrome and hook server types, and anything that only makes sense for a household running several services. The rest waits its turn. I send them in small batches, so a maintainer is never looking at a pile.

## How it is maintained

Images publish to `ghcr.io/splitsec2/wizarr:sha-<7>` from `build/fork-images`. That branch is a released upstream tag with each fork branch cherry-picked on top in a fixed order, then the CI commit. A script assembles it, runs ruff, format, djlint, a type check that must be no worse than upstream's and upstream's test suite on the combined tree, and only then pushes. PR branches are never rebased or force-pushed once a PR is open.

The build sits on upstream `v2026.10.1` and has not moved to newer upstream yet. Upstream restructured the Create Invitation modal since then, and the fork's four-step version of it has to be reconciled first.

Rollback is the previous `sha-<7>` tag, or upstream's own image. A build that carries a database migration needs the matching pre-migration database copy restored first.

Licence and copyright are upstream's, unchanged.

---

# Wizarr
> 🚀 **Development Relaunched:** We're excited to announce that **Wizarr is back in active development**!
<br></br>
<p align="center">

<img src="./app/static/wizarr-logo.png" height="200">
  <p></p>
</p>
<br></br>

[![GitHub last commit](https://img.shields.io/github/release-date/wizarrrr/wizarr?style=for-the-badge&logo=github)](https://github.com/wizarrrr/wizarr)
[![GitHub last release](https://img.shields.io/github/v/release/wizarrrr/wizarr?style=for-the-badge&logo=github)](https://github.com/wizarrrr/wizarr)
[![GitHub Repo stars](https://img.shields.io/github/stars/wizarrrr/wizarr?style=for-the-badge&logo=github)](https://github.com/wizarrrr/wizarr)
[![Discord](https://img.shields.io/discord/1020742926856372224?style=for-the-badge&logo=discord)](https://discord.gg/NYxwcjCK9x)


---

Wizarr is an automatic user invitation and management system for Plex, Jellyfin, Emby, Audiobookshelf, Romm, Komga and Kavita.  
Create a unique invite link and share it with users — they’ll be automatically added to your media server and guided through downloading apps, accessing request systems, and more!

**The problem**: Inviting your Friends/Family to your Plex server Is complicated and tedious. It's also a hard concept for them to get their head around.

**The solution**: Wizarr makes it easy to invite users to your server by simply sending them a link, and guides them through the process of getting set up.

---

## ✨ Major Features

- Automatic invitations for Plex, Jellyfin, Emby, AudiobookShelf, Komga, Kavita and Romm
- Secure, user-friendly invitation process
- Plug-and-play SSO support*
- Multi-tiered invitation access
- Time-limited membership options
- Pre-invite and post-invite wizard steps
- Setup guide for media apps (like Plex)
- Request system integration (Seerr, Ombi, etc.)
- Discord invite support
- Fully customisable with your own HTML snippets

---

## 🚀 Getting Started

Check out our documentation for how to install and run Wizarr:

📚 https://docs.wizarr.dev/getting-started/installation

## 🔧 API Documentation

Wizarr provides a comprehensive REST API for automation and integration with **automatic OpenAPI/Swagger documentation**:

📖 **Interactive API Documentation**: `http://your-wizarr-instance/api/docs/`  
📋 **OpenAPI Specification**: `http://your-wizarr-instance/api/swagger.json`

---

## Sponsorship
Thank you to our sponsor for supporting Wizarr development!

<a href="https://elfhosted.com">
  <img src="https://store.elfhosted.com/wp-content/uploads/2024/11/logo.svg" height="50" alt="Elfhosted" />
</a>
<a href="https://www.kwickflix.tv">
  <img src=".github/sponsors/kwickflix.tv.png" height="50" alt="kwickflix.tv" />
</a>


---

## 🌍 Translations

We use Weblate to make Wizarr accessible in many languages.  
Want to help translate? Click below!

<a href="https://hosted.weblate.org/engage/wizarr/">
<img src="https://hosted.weblate.org/widget/wizarr/wizarr-universal/287x66-grey.png" alt="Translation status" />
</a>

---

## ❤️ Thank You

A big thank you to these amazing contributors who’ve helped build and maintain this project:

<a href="https://github.com/wizarrrr/wizarr/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=wizarrrr/wizarr" />
</a>

---

## 📸 Screenshots

![Accept Invite](./screenshots/new-accept-plex-invite.png)
![Home](./screenshots/dashboard.png)
![Invitations](./screenshots/invitations.png)
![Settings](./screenshots/edit-servers.png)
![Users](./screenshots/users.png)
![Wizarr](./screenshots/wizard.jpeg)
![Edit Wizard](./screenshots/edit-wizard.png)
