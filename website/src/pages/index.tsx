import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import styles from './index.module.css';

const features = [
  {
    title: '🤖 Multi-Agent AI',
    description: 'Independent decision-making per trading pair with LangGraph. Supports OpenAI, NVIDIA, Google, Cloudflare, LiteLLM.',
  },
  {
    title: '📊 Grid Flow Strategy',
    description: 'AI-driven dynamic grid market making. LLM judges direction & width, math engine calculates parameters, GridManager handles orders.',
  },
  {
    title: '🔬 Research-Backed',
    description: 'FinCoT reasoning (+17% accuracy), Bull/Bear debate, CEX leading signals, Regime-adaptive strategies — all grounded in academic research.',
  },
  {
    title: '📈 Backtesting',
    description: 'Full backtest support for both perpetual and grid strategies, with A/B comparison and resume-from-checkpoint.',
  },
  {
    title: '🛡️ Risk Management',
    description: 'Kelly formula position sizing, ATR dynamic stop-loss/take-profit, max drawdown protection, position timeout.',
  },
  {
    title: '🐳 Docker Ready',
    description: 'One-command deployment with Docker Compose. Run main, grid, or both strategies simultaneously via RUN_MODE.',
  },
];

function Feature({title, description}: {title: string; description: string}) {
  return (
    <div className={clsx('col col--4', styles.feature)}>
      <div className="text--left padding-horiz--md padding-vert--sm">
        <Heading as="h3">{title}</Heading>
        <p>{description}</p>
      </div>
    </div>
  );
}

function HomepageHeader() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero hero--primary', styles.heroBanner)}>
      <div className="container">
        <Heading as="h1" className="hero__title">
          {siteConfig.title}
        </Heading>
        <p className="hero__subtitle">{siteConfig.tagline}</p>
        <div className={styles.buttons}>
          <Link className="button button--secondary button--lg" to="/docs/intro">
            Get Started →
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            href="https://github.com/loadchange/quant-flow">
            GitHub ★
          </Link>
        </div>
        <p className={styles.disclaimer}>
          ⚠️ For educational and research purposes only. Use at your own risk.
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
      description="AI-powered crypto trading bot for Hyperliquid DEX, built with LangChain/LangGraph">
      <HomepageHeader />
      <main>
        <section className={styles.features}>
          <div className="container">
            <div className="row">
              {features.map((f, idx) => (
                <Feature key={idx} {...f} />
              ))}
            </div>
          </div>
        </section>
      </main>
    </Layout>
  );
}
