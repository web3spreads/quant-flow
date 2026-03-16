import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'Quant Flow',
  tagline: 'AI-powered crypto trading bot for Hyperliquid DEX',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  url: 'https://web3spreads.github.io',
  baseUrl: '/quant-flow/',

  organizationName: 'web3spreads',
  projectName: 'quant-flow',
  trailingSlash: false,

  onBrokenLinks: 'throw',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh-Hans'],
    localeConfigs: {
      en: { label: 'English', direction: 'ltr' },
      'zh-Hans': { label: '中文', direction: 'ltr' },
    },
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/web3spreads/quant-flow/edit/main/website/',
          routeBasePath: 'docs',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/social-card.png',
    colorMode: {
      respectPrefersColorScheme: true,
    },
    announcementBar: {
      id: 'disclaimer',
      content: '⚠️ For educational and research purposes only. Use at your own risk in production.',
      backgroundColor: '#fff3cd',
      textColor: '#856404',
      isCloseable: true,
    },
    navbar: {
      title: 'Quant Flow',
      logo: {
        alt: 'Quant Flow Logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docs',
          position: 'left',
          label: 'Docs',
        },
        {
          type: 'localeDropdown',
          position: 'right',
        },
        {
          href: 'https://github.com/web3spreads/quant-flow',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Documentation',
          items: [
            { label: 'Introduction', to: '/docs/intro' },
            { label: 'Getting Started', to: '/docs/getting-started/docker' },
            { label: 'Configuration', to: '/docs/configuration/env' },
          ],
        },
        {
          title: 'Strategies',
          items: [
            { label: 'Perpetual Agent', to: '/docs/strategies/perpetual-agent' },
            { label: 'Grid Flow', to: '/docs/strategies/grid-flow' },
            { label: 'Backtesting', to: '/docs/backtesting/single' },
          ],
        },
        {
          title: 'Links',
          items: [
            { label: 'GitHub', href: 'https://github.com/web3spreads/quant-flow' },
            { label: 'Hyperliquid', href: 'https://hyperliquid.xyz/' },
            { label: 'Testnet Faucet', href: 'https://app.hyperliquid-testnet.xyz/faucet' },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Quant Flow. Built with Docusaurus. For educational purposes only.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'yaml', 'python', 'typescript'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
