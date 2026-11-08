# 代理池与精确位置交付切片

date: 2026-11-08
requirements: ../requirements/proxy-pool-location.md
design: ../proxy-pool-location-design.md

| 切片 | 验收 | 状态 |
|---|---|---|
| 三节点代理池和数据面故障切换 | AC1–AC4、AC6 | 已实现，单测与真实 socket 验证通过 |
| 精确地区/城市及管理面 | AC5、AC7 | 已实现，生产 manager 浏览器集成通过 |
| 最终整合与运行验证 | AC8 | 120 单测、浏览器回归、受控快照验证；审查修正及最终复审记录单独保存 |

共享位置字段及池 snapshot 先按设计固定；两类写者在隔离工作区施工，最终整合后执行全量回归与独立审查。完整交付包含两个能力，不以其中一个完成替代整单。

运行证据、polish 与未覆盖环境见 [验证记录](../proxy-pool-location-verification.md)。本次未重启现有服务或使用真实付费上游；审查原始输出保存在 `.local/proxy-pool-location/`。
