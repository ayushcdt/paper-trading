import type { Metadata } from 'next'
import './globals.css'
import Sidebar from '@/components/Sidebar'
import FreshnessBanner from '@/components/FreshnessBanner'

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
        <div className="flex">
          <Sidebar />
          <main className="flex-1 ml-64 p-8">
            <FreshnessBanner />
            {children}
          </main>
        </div>
      </body>
    </html>
  )
}
