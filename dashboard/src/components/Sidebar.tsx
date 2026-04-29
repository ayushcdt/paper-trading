'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  LayoutDashboard,
  TrendingUp,
  PieChart,
  BarChart3,
  Award,
  FlaskConical,
  Brain,
  Briefcase,
  Bell,
  Eye,
  Newspaper
} from 'lucide-react'

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Stock Picks', href: '/stocks', icon: TrendingUp },
  { name: 'Paper Trading', href: '/paper-trading', icon: Briefcase },
  { name: 'Alerts', href: '/alerts', icon: Bell },
  { name: 'News', href: '/news', icon: Newspaper },
  { name: 'News Shadow', href: '/news-shadow', icon: Eye },
  { name: 'Mutual Funds', href: '/mutualfunds', icon: PieChart },
  { name: 'Market Analysis', href: '/market', icon: BarChart3 },
  { name: 'Strategy', href: '/adaptive', icon: Brain },
  { name: 'Track Record', href: '/performance', icon: Award },
  { name: 'Backtest', href: '/backtest', icon: FlaskConical },
]

export default function Sidebar() {
  const pathname = usePathname()

  return (
    <div className="fixed inset-y-0 left-0 w-64 bg-white border-r border-gray-200">
      {/* Logo */}
      <div className="flex items-center gap-3 px-6 py-5 border-b border-gray-100">
        <div className="w-10 h-10 bg-gradient-to-br from-green-500 to-emerald-600 rounded-xl flex items-center justify-center">
          <span className="text-white font-bold text-lg">A</span>
        </div>
        <div>
          <h1 className="font-bold text-gray-900">Artha</h1>
          <p className="text-xs text-gray-500">Trading Dashboard</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="px-4 py-6 space-y-1">
        {navigation.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.name}
              href={item.href}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg transition-colors ${
                isActive
                  ? 'bg-green-50 text-green-700'
                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
              }`}
            >
              <item.icon className={`w-5 h-5 ${isActive ? 'text-green-600' : ''}`} />
              <span className="font-medium">{item.name}</span>
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="absolute bottom-0 left-0 right-0 p-4 border-t border-gray-100">
        <div className="px-4 py-3 bg-gray-50 rounded-lg">
          <p className="text-xs text-gray-500">Powered by</p>
          <p className="text-sm font-medium text-gray-700">Artha 2.0 + Claude</p>
        </div>
      </div>
    </div>
  )
}
