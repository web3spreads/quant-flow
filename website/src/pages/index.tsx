import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import Translate, {translate} from '@docusaurus/Translate';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import styles from './index.module.css';

interface FeatureItem {
  title: string;
  description: string;
}

function getFeatures(): FeatureItem[] {
  return [
    {
      title: translate({id: 'feature.multiAgent.title', message: 'Multi-Agent AI Graph'}),
      description: translate({
        id: 'feature.multiAgent.desc',
        message: 'Decentralized multi-agent architecture built on LangGraph. Runs independent decision trees per asset with context compression.',
      }),
    },
    {
      title: translate({id: 'feature.gridFlow.title', message: 'Grid Flow Market Maker'}),
      description: translate({
        id: 'feature.gridFlow.desc',
        message: 'AI-guided dynamic grid market making. LLM predicts boundaries while our math engine optimizes intervals and reduce-only layered exit orders.',
      }),
    },
    {
      title: translate({id: 'feature.fincot.title', message: 'FinCoT Reasoning'}),
      description: translate({
        id: 'feature.fincot.desc',
        message: 'Financial Chain-of-Thought reasoning. Drives 6-step structured decision-making, improving LLM trade accuracy by up to 17.3%.',
      }),
    },
    {
      title: translate({id: 'feature.debate.title', message: 'Bull/Bear Debate'}),
      description: translate({
        id: 'feature.debate.desc',
        message: 'Multi-agent adversarial debate. Dual independent LLM agents challenge trade assumptions to eliminate confirmation bias.',
      }),
    },
    {
      title: translate({id: 'feature.backtesting.title', message: 'Deterministic Backtesting'}),
      description: translate({
        id: 'feature.backtesting.desc',
        message: 'Comprehensive backtest engine with support for A/B config comparisons, decision recording, and precise checkpoint replays.',
      }),
    },
    {
      title: translate({id: 'feature.risk.title', message: 'Institutional Risk Guard'}),
      description: translate({
        id: 'feature.risk.desc',
        message: 'Advanced protection layer featuring Kelly position sizing, ATR-based SL/TP, drawdown circuit breakers, and position timeout.',
      }),
    },
  ];
}

function FeatureCard({title, description, index}: FeatureItem & {index: number}) {
  const numStr = String(index + 1).padStart(2, '0');
  return (
    <div className="col col--4 margin-bottom--lg">
      <div className={styles.featureCard}>
        <span className={styles.featureNumber}>// {numStr}</span>
        <h3 className={styles.featureTitle}>{title}</h3>
        <p className={styles.featureDesc}>{description}</p>
      </div>
    </div>
  );
}

function TerminalMockup() {
  return (
    <div className={styles.terminalMockup}>
      <div className={styles.terminalHeader}>
        <div className={styles.terminalDot + ' ' + styles.dotRed}></div>
        <div className={styles.terminalDot + ' ' + styles.dotYellow}></div>
        <div className={styles.terminalDot + ' ' + styles.dotGreen}></div>
        <span className={styles.terminalTitle}>quant-flow-bot — zsh</span>
      </div>
      <div className={styles.terminalBody}>
        <p className={styles.terminalLine}>
          <span className={styles.lineCmd}>$ uv run python main.py --symbol BTC</span>
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>[2026-06-14 08:00:00]</span> 📡 <span className={styles.lineInfo}>[System]</span> Initializing Quant Flow Multi-Agent Engine...
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>[2026-06-14 08:00:02]</span> 🤖 <span className={styles.lineInfo}>[Agent]</span> Starting decision graph for BTC
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>[2026-06-14 08:00:03]</span> 📈 <span className={styles.lineInfo}>[Regime]</span> State: <span className={styles.lineSuccess}>TRENDING_BULLISH</span>
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>[2026-06-14 08:00:05]</span> ⚔️ <span className={styles.lineInfo}>[Debate]</span> Bull vs Bear debate started:
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>   - [Bull]</span> Breakout above EMA(20) on 4h timeframe confirmed with high volume.
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>   - [Bear]</span> RSI is near 68, showing short-term exhaustion. Prefer pullback entry.
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>   - [Consensus]</span> Bullish momentum dominant. Proceeding to buy.
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>[2026-06-14 08:00:08]</span> 🧠 <span className={styles.lineInfo}>[FinCoT]</span> Action: <span className={styles.lineSuccess}>BUY</span> | Price: 95,430 USDT | Size: 0.12 BTC
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>[2026-06-14 08:00:09]</span> 🛡️ <span className={styles.lineInfo}>[Risk]</span> ATR SL/TP configured. SL: 92,100 | TP: 102,000. Risk check passed.
        </p>
        <p className={styles.terminalLine}>
          <span className={styles.lineDim}>[2026-06-14 08:00:10]</span> ⚡ <span className={styles.lineSuccess}>[Execute]</span> Order executed successfully on Hyperliquid DEX.
        </p>
      </div>
    </div>
  );
}

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={styles.heroBanner}>
      <div className="container">
        <div className={styles.heroLogoContainer}>
          <img
            src="img/logo-light.svg"
            alt="Quant Flow Logo"
            className={styles.heroLogo + ' ' + styles.logoLightOnly}
          />
          <img
            src="img/logo-dark.svg"
            alt="Quant Flow Logo"
            className={styles.heroLogo + ' ' + styles.logoDarkOnly}
          />
        </div>
        <h1 className="hero__title">
          {siteConfig.title}
        </h1>
        <p className={styles.heroTagline}>
          <Translate id="homepage.tagline">
            Next-generation AI-powered quantitative trading system built with LangGraph for Hyperliquid DEX.
          </Translate>
        </p>
        <div className={styles.buttons}>
          <Link className={styles.btnPrimary} to="/docs/intro">
            <Translate id="homepage.getStarted">Read Documentation</Translate>
          </Link>
          <Link
            className={styles.btnSecondary}
            href="https://github.com/web3spreads/quant-flow">
            GitHub Repo
          </Link>
        </div>
        <p className={styles.disclaimer}>
          <Translate id="homepage.disclaimer">
            ⚠️ DISCLAIMER: For educational & research purposes only. Crypto trading involves high risk.
          </Translate>
        </p>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout
      title={siteConfig.title}
      description="Next-generation AI-powered quantitative trading system built with LangGraph for Hyperliquid DEX">
      <HomepageHeader />
      <main>
        {/* Core Features */}
        <section className={styles.featuresSection}>
          <div className="container">
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                <Translate id="homepage.features.title">Core Infrastructure</Translate>
              </h2>
              <p className={styles.sectionSubtitle}>
                <Translate id="homepage.features.subtitle">
                  Fusing state-of-the-art AI orchestration with institutional-grade risk parameters.
                </Translate>
              </p>
            </div>
            <div className="row">
              {getFeatures().map((feature, idx) => (
                <FeatureCard key={idx} index={idx} {...feature} />
              ))}
            </div>
          </div>
        </section>

        {/* Live Demo & Terminal Mockup Showcase */}
        <section className={styles.showcaseSection}>
          <div className="container">
            <div className={styles.showcaseGrid}>
              <div className={styles.showcaseContent}>
                <span className={styles.showcaseTag}>Real-time Execution</span>
                <h2 className={styles.showcaseTitle}>
                  Multi-Agent Cognitive Framework
                </h2>
                <p className={styles.showcaseText}>
                  Quant Flow replaces static trading rules with an autonomous cognitive graph.
                  Every trade cycle triggers a coordinated chain of agent events to ensure rationality,
                  eliminate biases, and maintain rigorous capital protections.
                </p>
                <ul className={styles.showcaseList}>
                  <li className={styles.showcaseListItem}>
                    <strong>Regime Adaptive:</strong> Dynamic market regime classification (Trending vs Ranging).
                  </li>
                  <li className={styles.showcaseListItem}>
                    <strong>Adversarial Debate:</strong> Eliminates confirmation bias via Bull/Bear dialectic.
                  </li>
                  <li className={styles.showcaseListItem}>
                    <strong>Verbal Fine-Tuning:</strong> Short-term memories and lessons feed back into subsequent prompts.
                  </li>
                </ul>
              </div>
              <div>
                <TerminalMockup />
              </div>
            </div>
          </div>
        </section>
      </main>
    </Layout>
  );
}
