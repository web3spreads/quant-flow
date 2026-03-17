import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import Translate, {translate} from '@docusaurus/Translate';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import styles from './index.module.css';

function getFeatures() {
  return [
    {
      title: translate({id: 'feature.multiAgent.title', message: '🤖 Multi-Agent AI'}),
      description: translate({
        id: 'feature.multiAgent.desc',
        message: 'Independent decision-making per trading pair with LangGraph. Supports OpenAI, NVIDIA, Google, Cloudflare, LiteLLM.',
      }),
    },
    {
      title: translate({id: 'feature.gridFlow.title', message: '📊 Grid Flow Strategy'}),
      description: translate({
        id: 'feature.gridFlow.desc',
        message: 'AI-driven dynamic grid market making. LLM judges direction & width, math engine calculates parameters, GridManager handles orders.',
      }),
    },
    {
      title: translate({id: 'feature.research.title', message: '🔬 Research-Backed'}),
      description: translate({
        id: 'feature.research.desc',
        message: 'FinCoT reasoning (+17% accuracy), Bull/Bear debate, CEX leading signals, Regime-adaptive strategies — all grounded in academic research.',
      }),
    },
    {
      title: translate({id: 'feature.backtest.title', message: '📈 Backtesting'}),
      description: translate({
        id: 'feature.backtest.desc',
        message: 'Full backtest support for both perpetual and grid strategies, with A/B comparison and resume-from-checkpoint.',
      }),
    },
    {
      title: translate({id: 'feature.risk.title', message: '🛡️ Risk Management'}),
      description: translate({
        id: 'feature.risk.desc',
        message: 'Kelly formula position sizing, ATR dynamic stop-loss/take-profit, max drawdown protection, position timeout.',
      }),
    },
    {
      title: translate({id: 'feature.docker.title', message: '🐳 Docker Ready'}),
      description: translate({
        id: 'feature.docker.desc',
        message: 'One-command deployment with Docker Compose. Run main, grid, or both strategies simultaneously via RUN_MODE.',
      }),
    },
  ];
}

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
        <p className="hero__subtitle">
          <Translate id="homepage.tagline">{siteConfig.tagline}</Translate>
        </p>
        <div className={styles.buttons}>
          <Link className="button button--secondary button--lg" to="/docs/intro">
            <Translate id="homepage.getStarted">Get Started →</Translate>
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            href="https://github.com/web3spreads/quant-flow">
            GitHub ★
          </Link>
        </div>
        <p className={styles.disclaimer}>
          <Translate id="homepage.disclaimer">
            ⚠️ For educational and research purposes only. Use at your own risk.
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
      description={translate({
        id: 'homepage.description',
        message: 'AI-powered crypto trading bot for Hyperliquid DEX, built with LangChain/LangGraph',
      })}>
      <HomepageHeader />
      <main>
        <section className={styles.features}>
          <div className="container">
            <div className="row">
              {getFeatures().map((f, idx) => (
                <Feature key={idx} {...f} />
              ))}
            </div>
          </div>
        </section>
      </main>
    </Layout>
  );
}
