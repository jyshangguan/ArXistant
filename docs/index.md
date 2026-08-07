---
layout: default
title: Documentation
description: Install, use, and understand ArXistant—the local personalized arXiv assistant.
permalink: /
---

<section class="hero">
  <img src="{{ '/logo0.5.jpg' | relative_url }}" alt="ArXistant logo" class="hero-logo">
  <div>
    <p class="eyebrow">ArXistant documentation</p>
    <h1>Your personalized astronomy reading assistant</h1>
    <p class="hero-copy">Install ArXistant, build a useful saved-paper library, and understand how its local ranking model turns new arXiv submissions into a focused daily reading list.</p>
    <div class="hero-actions">
      <a class="button primary" href="{{ '/installation/' | relative_url }}">Install ArXistant</a>
      <a class="button secondary" href="{{ '/user-guide/' | relative_url }}">Read the user guide</a>
    </div>
  </div>
</section>

## Start here

<div class="doc-grid">
  <a class="doc-card" href="{{ '/installation/' | relative_url }}">
    <span class="card-number">01</span>
    <h3>Installation</h3>
    <p>Set up the Chrome extension and local server on macOS, Debian/Ubuntu, Windows, or another Linux system.</p>
  </a>
  <a class="doc-card" href="{{ '/user-guide/' | relative_url }}">
    <span class="card-number">02</span>
    <h3>User guide</h3>
    <p>Work through daily papers, reminders, saved items, ADS search, publication import, and model controls.</p>
  </a>
  <a class="doc-card" href="{{ '/how-it-works/' | relative_url }}">
    <span class="card-number">03</span>
    <h3>How it works</h3>
    <p>Explore the local architecture, ranking pipeline, training lifecycle, data storage, and platform startup.</p>
  </a>
</div>

## What stays local

ArXistant runs a small server on your computer. Your saved-paper database,
ranking model, manual keywords, notes, and generated pages remain in its local
data directory. Network requests go directly to arXiv and, when configured,
NASA ADS.

## Current platform support

| Platform | Installation | Automatic server startup |
|---|---|---|
| macOS | Repository checkout | Bundled helper application |
| Debian/Ubuntu | Experimental `.deb` package | systemd user service |
| Other Linux | Manual Python setup | User-configured |
| Windows | Manual Python setup | Not yet available |

Ready to begin? Continue to the [installation guide]({{ '/installation/' | relative_url }}).
