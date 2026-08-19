import sys
sys.path.insert(0, 'src')
from PySide6.QtWidgets import QApplication
from sim_runner import SimulatorWindow
app = QApplication([])
w = SimulatorWindow()
w.show()
app.processEvents()
g = w.frameGeometry()
print('sim geometry:', g.x(), g.y(), g.width(), g.height())
screen = app.primaryScreen()
print('screen:', screen.availableGeometry().x(), screen.availableGeometry().y(), screen.availableGeometry().width(), screen.availableGeometry().height())
print('runner pos:', w._runner.x(), w._runner.y())
w.close()