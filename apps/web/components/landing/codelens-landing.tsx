'use client'

import { useState, useEffect, useRef, Fragment } from 'react'
import Link from 'next/link'

const features = [
  ['01', 'Repository intelligence', 'Connect repositories and build a searchable understanding of their source code.', 'tree'],
  ['02', 'Static analysis', 'Detect bugs, security issues, maintainability problems, and quality findings automatically.', 'findings'],
  ['03', 'AI explanations', 'Turn individual findings into clear explanations grounded in relevant source code.', 'answer'],
  ['04', 'AI fixes', 'Generate suggested code changes without immediately modifying your repository.', 'diff'],
  ['05', 'Test generation', 'Generate tests for detected issues and validate proposed changes.', 'test'],
  ['06', 'Pull request review', 'Review changed files, identify risks, and generate structured reviews.', 'review'],
]

function Mark() {
  return <span className="mark" aria-hidden="true"><i /><i /><i /></span>
}

function Navbar() {
  const [open, setOpen] = useState(false)
  return (
    <header className="nav-outer" aria-label="Site header">
      <div className="nav-pill">
        <Link className="brand" href="/"><Mark /><span>CODELENS</span></Link>
        <nav className={open ? 'nav-links open' : 'nav-links'} aria-label="Primary navigation">
          <a href="#platform" onClick={() => setOpen(false)}>Platform</a>
          <a href="#features" onClick={() => setOpen(false)}>Features</a>
          <a href="#how" onClick={() => setOpen(false)}>How it works</a>
          <a href="#security" onClick={() => setOpen(false)}>Security</a>
        </nav>
        <div className="nav-actions">
          <Link className="nav-login" href="/login">Sign in <span aria-hidden="true">&#8599;</span></Link>
        </div>
        <button className="menu-button" aria-label="Toggle navigation" onClick={() => setOpen(!open)}><span /><span /></button>
      </div>
    </header>
  )
}


function ProductPanel() {
  return <div className="product-shell">
      <div className="lp-scan-line" />
    <div className="panel-top"><span className="window-dots"><i /><i /><i /></span><span className="mono muted">CODELENS / ASK</span><span className="live"><b /> INDEXED</span></div>
    <div className="ask-panel">
      <div className="ask-label"><span className="terminal">&gt;_</span><span>Ask CodeLens about your repository</span></div>
      <div className="question">How does authentication work in this repository?<span className="cursor" /></div>
      <div className="answer-preview"><div className="answer-line"><span className="blue-dot" /> <span>Authentication is handled through the auth router and protected session middleware.</span></div><div className="source-row"><span>source</span><code>apps/api/app/api/routes/auth.py</code><span>lines 24–58</span></div></div>
      <div className="panel-footer"><span className="mono muted">REPOSITORY CONTEXT · 18 FILES</span><button className="button ask-button">Ask CodeLens <span>→</span></button></div>
    </div>
  </div>
}

function MiniVisual({ type }: { type: string }) {
  if (type === 'tree') return <div className="mini-tree mono"><span>⌄ src</span><span>  ⌄ api</span><span className="cyan">    auth.py</span><span>  ⌄ core</span><span>    security.py</span></div>
  if (type === 'findings') return <div className="mini-findings"><span className="error">ERROR <b>02</b></span><span className="warn">WARN <b>09</b></span><span className="info">INFO <b>04</b></span></div>
  if (type === 'answer') return <div className="mini-answer"><span className="cyan">›</span> Finding explained <span className="line" /></div>
  if (type === 'diff') return <div className="mini-diff mono"><span className="red">− except:</span><span className="green">+ except Exception as err:</span><span>+     log(err)</span></div>
  if (type === 'test') return <div className="mini-test mono"><span className="cyan">test_auth_rejects_invalid_token</span><span>assert response.status == 401</span></div>
  return <div className="mini-review"><span className="cyan">PR #142</span><span>risk / <b>medium</b></span></div>
}

function FeatureCard({ feature }: { feature: string[] }) {
  return <article className="feature-card"><div className="feature-number mono">{feature[0]}</div><h3>{feature[1]}</h3><p>{feature[2]}</p><MiniVisual type={feature[3]} /></article>
}

export default function CodeLensLanding() {

  const revealRefs = useRef<HTMLElement[]>([])
  const progressRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const els = revealRefs.current.filter(Boolean)
    if (!els.length) return

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('revealed')
        }
      })
    }, { threshold: 0.12, rootMargin: '0px 0px -50px 0px' })

    els.forEach(el => observer.observe(el))

    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const handleScroll = () => {
      if (!progressRef.current) return
      const scrolled = document.documentElement.scrollTop
      const height = document.documentElement.scrollHeight - document.documentElement.clientHeight
      const progress = Math.min(Math.max(scrolled / height, 0), 1)
      progressRef.current.style.transform = `scaleX(${progress})`
    }
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  const addReveal = (el: HTMLElement | null) => {
    if (el && !revealRefs.current.includes(el)) {
      revealRefs.current.push(el)
    }
  }

  return <div className="landing-wrapper">
    <div className="lp-progress-bar" ref={progressRef} aria-hidden="true" />
    <main id="top" className="site-shell">
    <section className="hero-section"><div className="hero-grid" /><div className="hero-glow" /><Navbar /><div className="hero-copy"><div className="eyebrow"><span /> AI-POWERED CODE INTELLIGENCE</div><h1>Understand your<br /><em>codebase.</em> <strong>Ship better code.</strong></h1><p>CodeLens analyzes your repositories, finds what matters, and helps you move from understanding to action with AI grounded in your actual code.</p><div className="hero-actions"><Link className="button" href="/login">Connect GitHub <span>↗</span></Link><Link className="text-link" href="/dashboard">Explore the platform <span>↓</span></Link></div></div><ProductPanel /><div className="capability-row"><span>GitHub repositories</span><i /><span>Static analysis</span><i /><span>AI intelligence</span><i /><span>Pull request review</span></div></section>

    <section ref={addReveal} className="intro section-pad lp-reveal"><div className="section-kicker">01 / THE INTELLIGENCE LAYER</div><div className="intro-grid"><h2>Your codebase has answers.<br /><span>CodeLens helps you find them.</span></h2><p>From the first scan to the final pull request, CodeLens turns your repository into actionable engineering insight.</p></div><div className="flow-strip lp-stagger">{[['01','SCAN','Connect a GitHub repository and ingest its source files.'],['02','ANALYZE','Detect bugs, security issues, and quality findings.'],['03','UNDERSTAND','Ask questions with context from your actual repository.'],['04','SHIP','Generate fixes, tests, and pull-request reviews.']].map(([n,t,d]) => <div key={n}><span className="mono blue-text">{n}</span><h3>{t}</h3><p>{d}</p></div>)}</div></section>

    <section id="platform" ref={addReveal} className="platform section-pad lp-reveal"><div className="section-kicker">02 / PLATFORM</div><div className="split-heading"><h2>One intelligence layer<br /><span>for your codebase.</span></h2><p>Repository context, static analysis, and AI reasoning — connected in one precise workflow.</p></div><div className="architecture lp-stagger"><div className="arch-node"><span>01</span><b>GitHub repository</b><small>source files · history · pull requests</small></div><div className="arch-line" /><div className="arch-node active"><span>02</span><b>CodeLens</b><small>ingest · index · retrieve</small></div><div className="arch-line" /><div className="arch-node"><span>03</span><b>Analysis + AI</b><small>findings · explanations · context</small></div><div className="arch-line" /><div className="arch-node"><span>04</span><b>Action</b><small>fixes · tests · PR review</small></div></div></section>

    <section id="features" ref={addReveal} className="features section-pad lp-reveal"><div className="section-kicker">03 / CAPABILITIES</div><div className="split-heading"><h2>Everything you need to<br /><span>understand and improve code.</span></h2><p>Designed for the moments when a codebase is too large to hold in your head — and too important to guess about.</p></div><div className="feature-grid lp-stagger">{features.map((f) => <FeatureCard key={f[0]} feature={f} />)}</div></section>

    <section ref={addReveal} className="ask-section section-pad lp-reveal"><div className="ask-orbit" /><div className="section-kicker">04 / ASK CODELENS</div><div className="center-heading"><h2>Ask your codebase<br /><span>anything.</span></h2><p>Answers grounded in retrieved repository code, with the source references to prove it.</p></div><div className="ask-showcase"><div className="question-list"><span className="mono muted">QUESTIONS TO START WITH</span>{['How does authentication work?','Where are API requests handled?','Where is the database connection created?','Which files handle GitHub integration?'].map((q, i) => <button key={q} className={i === 0 ? 'selected' : ''}>{q}<span>→</span></button>)}</div><div className="answer-card"><div className="answer-card-top"><span className="live"><b /> GROUNDED IN YOUR REPOSITORY</span><span className="mono muted">0.8s</span></div><h3>How does authentication work?</h3><p>Requests pass through the auth router, where session tokens are validated before protected routes are reached. The session middleware attaches the current user to the request context.</p><div className="refs"><span className="mono muted">SOURCE REFERENCES</span><code>apps/api/app/api/routes/auth.py <small>LINES 20–58</small></code><code>apps/api/app/core/security.py <small>LINES 12–41</small></code></div></div></div></section>

    <section ref={addReveal} className="analysis section-pad lp-reveal"><div className="analysis-card"><div className="analysis-head"><div><span className="mono muted">EXAMPLE ANALYSIS</span><h3>Repository health</h3></div><strong>87 <small>/ 100</small></strong></div><div className="analysis-body"><div className="stats lp-stagger"><span><b className="red-text">02</b> Errors</span><span><b className="yellow-text">09</b> Warnings</span><span><b className="blue-text">04</b> Info</span></div><div className="chart"><div className="chart-grid" /><svg viewBox="0 0 600 150" preserveAspectRatio="none" aria-label="Example findings trend"><path d="M0 120 C70 108, 95 60, 155 80 S250 50, 300 70 S395 28, 450 46 S535 15, 600 24" fill="none" stroke="#35a9ff" strokeWidth="2" /></svg><div className="chart-labels"><span>SCAN 01</span><span>SCAN 02</span><span>SCAN 03</span><span>SCAN 04</span><span>SCAN 05</span></div></div></div></div></section>

    <section id="how" ref={addReveal} className="timeline section-pad lp-reveal"><div className="section-kicker">05 / HOW IT WORKS</div><div className="split-heading"><h2>From repository<br /><span>to better decisions.</span></h2><p>A focused workflow that keeps your team close to the code and clear on what happens next.</p></div><div className="steps lp-stagger">{[['01','CONNECT','Connect your GitHub repository.'],['02','INGEST','Retrieve and store source files for analysis.'],['03','ANALYZE','Static analyzers inspect supported source files.'],['04','UNDERSTAND','AI explains findings using retrieved context.'],['05','IMPROVE','Generate fixes, tests, and reviews.']].map(([n,t,d]) => <div key={n} className="step"><span className="step-no mono">{n}</span><div><h3>{t}</h3><p>{d}</p></div></div>)}</div></section>

    <section id="security" ref={addReveal} className="security section-pad lp-reveal"><div className="security-copy"><div className="section-kicker">06 / SECURITY</div><h2>Built with<br /><span>security in mind.</span></h2><p>CodeLens keeps analysis scoped to the repository and the user who connected it, with controlled AI context at every step.</p></div><div className="security-list lp-stagger">{['Authenticated users','Repository ownership isolation','Protected API routes','Secure session cookies','Provider-neutral AI errors','Repository-scoped analysis'].map((item, i) => <div key={item}><span className="check">&#x2713;</span><span>{item}</span><span className="mono muted">0{i + 1}</span></div>)}</div></section>

    <section id="cta" ref={addReveal} className="final-cta section-pad lp-reveal"><div className="cta-glow" /><div className="eyebrow"><span /> READY WHEN YOU ARE</div><h2>Your code already has<br /><em>the answers.</em></h2><p>Connect a repository and start looking closer.</p><div className="hero-actions"><Link className="button" href="/login">Connect GitHub <span>↗</span></Link><Link className="text-link" href="/dashboard">Explore the platform <span>→</span></Link></div></section>

    <footer id="footer"><div className="footer-brand"><Link className="brand" href="/"><Mark /><span>CODELENS</span></Link><p>AI-powered code intelligence.</p></div><div className="footer-cols"><div><span className="mono muted">PRODUCT</span><a href="#platform">Platform</a><a href="#features">Features</a><a href="#how">How it works</a><a href="#security">Security</a></div><div><span className="mono muted">ACCOUNT</span><Link href="/login">Sign in</Link><Link href="/signup">Get started</Link></div></div><div className="footer-bottom"><span>© 2026 CodeLens</span><span className="mono">BUILT FOR THE CURIOUS</span></div></footer>
  </main>
  </div>
}
