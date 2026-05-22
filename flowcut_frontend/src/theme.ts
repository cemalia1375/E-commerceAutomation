import type { ThemeConfig } from 'antd'

export const theme: ThemeConfig = {
  token: {
    colorPrimary: '#2563eb',
    colorBgContainer: '#ffffff',
    colorBgLayout: '#f1f5f9',
    borderRadius: 10,
    borderRadiusSM: 6,
    borderRadiusLG: 14,
    fontFamily: "'Outfit', 'PingFang SC', -apple-system, sans-serif",
    fontSize: 14,
    colorBorder: '#e2e8f0',
    colorText: '#0f172a',
    colorTextSecondary: '#475569',
    colorTextTertiary: '#94a3b8',
    colorSuccess: '#059669',
    colorWarning: '#d97706',
    colorError: '#dc2626',
  },
  components: {
    Tabs: {
      cardBg: '#f1f5f9',
      itemSelectedColor: '#2563eb',
    },
  },
}
