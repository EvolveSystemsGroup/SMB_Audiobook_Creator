PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin
DATADIR ?= $(PREFIX)/share
APPID = app.sellmybook.smbaudiobookcreator

run: 
	python3 src/main.py

install:
	install -Dm755 src/main.py $(DESTDIR)$(BINDIR)/smb-ab-creator
	install -Dm644 data/gmb-page.png $(DESTDIR)$(DATADIR)/smb-ab-creator/data/gmb-page.png
	install -Dm644 data/app.sellmybook.smbaudiobookcreator.desktop $(DESTDIR)$(DATADIR)/applications/app.sellmybook.smbaudiobookcreator.desktop
	install -Dm644 data/app.sellmybook.smbaudiobookcreator.metainfo.xml $(DESTDIR)$(DATADIR)/metainfo/app.sellmybook.smbaudiobookcreator.metainfo.xml
	install -Dm644 data/app.sellmybook.smbaudiobookcreator.svg $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/app.sellmybook.smbaudiobookcreator.svg
	install -Dm644 data/app.sellmybook.smbaudiobookcreator.png $(DESTDIR)$(DATADIR)/icons/hicolor/512x512/apps/app.sellmybook.smbaudiobookcreator.png

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/smb-ab-creator
	rm -f $(DESTDIR)$(DATADIR)/smb-ab-creator/data/gmb-page.png
	rm -f $(DESTDIR)$(DATADIR)/applications/app.sellmybook.smbaudiobookcreator.desktop
	rm -f $(DESTDIR)$(DATADIR)/metainfo/app.sellmybook.smbaudiobookcreator.metainfo.xml
	rm -f $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/app.sellmybook.smbaudiobookcreator.svg
	rm -f $(DESTDIR)$(DATADIR)/icons/hicolor/512x512/apps/app.sellmybook.smbaudiobookcreator.png
