"""Clarify independent product scenario inputs in the business flow diagram."""
from build_report import Figure

def build_flow():
    f = Figure(1900, 1750)
    for box, title in [
        ((650, 50, 1250, 190), '导入历史交易数据'),
        ((40, 310, 560, 470), '反馈问题清单\n修正或确认处理'),
        ((650, 610, 1250, 750), '确认处理与保存批次'),
        ((650, 850, 1250, 990), '商品订单查询与看板'),
        ((50, 1140, 650, 1280), '全店销量预测\n与历史回测'),
        ((700, 1140, 1300, 1280), '用户设定商品场景需求\n与模拟库存参数'),
        ((1400, 1140, 1850, 1280), 'AI调用受控工具\n查询统计与预测'),
        ((700, 1350, 1300, 1470), '模拟库存与补货提示'),
        ((700, 1560, 1300, 1700), '核对并导出运营简报'),
    ]:
        f.box(box, title, 40)
    diamond = [(950, 260), (1200, 390), (950, 520), (700, 390)]
    f.draw.polygon(diamond, fill='white', outline='#222222', width=3)
    f.svg.append('<polygon points="' + ' '.join(f'{x},{y}' for x,y in diamond)
                 + '" fill="white" stroke="#222" stroke-width="3"/>')
    f.text((950, 390), '数据检查\n是否可入库', 40)
    f.line([(950,190),(950,260)], arrow=True)
    f.line([(700,390),(560,390)], arrow=True, label='否', label_xy=(630,345))
    f.line([(300,310),(300,120),(650,120)], arrow=True)
    f.line([(950,520),(950,610)], arrow=True, label='是', label_xy=(1010,565))
    f.line([(950,750),(950,850)], arrow=True)
    f.line([(950,990),(950,1060),(350,1060),(350,1140)], arrow=True)
    f.line([(950,1060),(1000,1060),(1000,1140)], arrow=True)
    f.line([(1000,1060),(1625,1060),(1625,1140)], arrow=True)
    f.line([(1000,1280),(1000,1350)], arrow=True)
    f.line([(1000,1470),(1000,1560)], arrow=True)
    f.line([(350,1280),(350,1630),(700,1630)], arrow=True)
    f.line([(1625,1280),(1625,1630),(1300,1630)], arrow=True)
    f.save('图3-3_系统主要业务流程图')

if __name__ == '__main__':
    build_flow()
