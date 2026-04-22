import type { Metadata } from 'next'
import './globals.css'
import Sidebar from '@/components/Sidebar'
import FreshnessBanner from '@/components/FreshnessBanner'
import LiveTickBadge from '@/components/LiveTickBadge'
import { LiveDataProvider } from '@/components/LiveDataProvider'
import { LiveDebugBadge } from '@/components/LiveDebugBadge'

export const metadata: Metadata = {
  title: 'Artha Dashboard - AI Stock Analysis',
  description: 'AI-powered stock picks and market analysis by Artha 2.0',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-50">
        <LiveDataProvider>
          <div className="flex">
            <Sidebar />
            <main className="flex-1 ml-64 p-8">
              <div className="mb-4 flex items-center gap-3 flex-wrap">
                <LiveTickBadge />
                <LiveDebugBadge />
              </div>
              <FreshnessBanner />
              {children}
            </main>
          </div>
        </LiveDataProvider>
      </body>
    </html>
  )
}
