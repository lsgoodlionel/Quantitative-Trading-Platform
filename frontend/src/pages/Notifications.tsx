import { Navigate } from "react-router-dom"

/**
 * `/notifications` → `/alerts?tab=inbox`
 *
 * 通知中心已并入「预警与通知」页（规则触发与通知送达本就是一件事的两半）。
 * 这里保留重定向而不是删掉路由：已有的浏览器书签、以及历史通知里发出去的
 * 深链都指向 `/notifications`，直接删会变成 404。
 *
 * `replace` 让它不进历史栈 —— 否则用户按「后退」会被弹回来再重定向一次，
 * 卡在两个地址之间出不去。
 */
export function Notifications() {
  return <Navigate to="/alerts?tab=inbox" replace />
}
