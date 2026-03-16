import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/docker',
        'getting-started/local',
      ],
    },
    {
      type: 'category',
      label: 'Configuration',
      items: [
        'configuration/env',
        'configuration/config-yaml',
        'configuration/grid-config',
      ],
    },
    {
      type: 'category',
      label: 'Trading Strategies',
      items: [
        'strategies/perpetual-agent',
        'strategies/grid-flow',
      ],
    },
    {
      type: 'category',
      label: 'AI Features',
      items: [
        'features/fincot',
        'features/debate',
        'features/cex-signals',
        'features/regime-adaptive',
        'features/market-monitor',
        'features/review-system',
      ],
    },
    {
      type: 'category',
      label: 'Backtesting',
      items: [
        'backtesting/single',
        'backtesting/grid',
        'backtesting/comparison',
      ],
    },
    {
      type: 'category',
      label: 'Architecture',
      items: [
        'architecture/overview',
        'architecture/modules',
      ],
    },
    'faq',
  ],
};

export default sidebars;
