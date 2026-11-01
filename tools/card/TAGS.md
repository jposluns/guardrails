# AIQT.ai link-preview tags

- og:title: AIQT.ai - Rules your AI follows
- og:description: Rules for your chat assistant, enforcement controls for your codebase.
- og:image:alt: AIQT.ai: Rules your AI follows. Controls that catch what slips.
- Image URL: https://aiqt.ai/images/aiqt-card.png
- card.png SHA-256: 83ef43f91fbd57daf2d9eb7fe0d1445bda17e8467b10a8bed6406c66c86e13bc (113729 bytes)

## Tag block for the page head

```html
<link rel="canonical" href="https://aiqt.ai/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="AIQT.ai">
<meta property="og:url" content="https://aiqt.ai/">
<meta property="og:title" content="AIQT.ai - Rules your AI follows">
<meta property="og:description" content="Rules for your chat assistant, enforcement controls for your codebase.">
<meta property="og:image" content="https://aiqt.ai/images/aiqt-card.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="AIQT.ai: Rules your AI follows. Controls that catch what slips.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="AIQT.ai - Rules your AI follows">
<meta name="twitter:description" content="Rules for your chat assistant, enforcement controls for your codebase.">
<meta name="twitter:image" content="https://aiqt.ai/images/aiqt-card.png">
<meta name="twitter:image:alt" content="AIQT.ai: Rules your AI follows. Controls that catch what slips.">
```

## Sources

The card image is site/images/aiqt-card.png, with the SHA-256 recorded above. Its sources are card.html
and illustration.svg in this folder. Every published site/*.html page except site/404.html carries the tag
block above in its head, in this order, with canonical and og:url set to that page's own URL. That covers
the pages tools/gen_site.py generates and the hand-maintained ones. tools/check_site.py checks every such
page when AIQT_SITE_HOST is unset, empty or names aiqt.ai (or www.aiqt.ai), and under any other site host
skips the check and prints a line saying so; a value that is not a plain host name (a port, a trailing
dot, or a character a host name cannot hold) is refused.
site/404.html is a noindex page, so it does not carry the block.

Each tag must write every spelling of its key exactly as the block above does: each rel token naming
canonical as `canonical` in lower case (other rel tokens may sit beside it), and each og: or twitter:
property or name value carrying the key in the same case with no surrounding whitespace. This is stricter
than HTML for rel, whose tokens compare case-insensitively, so a `rel="Canonical"` a browser would honour
fails the check.
