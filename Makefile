PREFIX ?= ~/.local
BINDIR ?= $(PREFIX)/bin
DATADIR ?= $(PREFIX)/share
APPID = app.sellmybook.smbaudiobookcreator
APPDIR = $(DATADIR)/smb-ab-creator
PY_SOURCES = $(wildcard src/*.py)

run: 
	python3 src/main.py

install:
	install -d $(DESTDIR)$(BINDIR)
	install -d $(DESTDIR)$(APPDIR)
	for file in $(PY_SOURCES); do install -m644 $$file $(DESTDIR)$(APPDIR)/$$(basename $$file); done
	chmod 755 $(DESTDIR)$(APPDIR)/main.py
	ln -sf $(APPDIR)/main.py $(DESTDIR)$(BINDIR)/smb-ab-creator
	install -Dm644 data/gmb-page.png $(DESTDIR)$(APPDIR)/data/gmb-page.png
	install -Dm644 data/app.sellmybook.smbaudiobookcreator.desktop $(DESTDIR)$(DATADIR)/applications/app.sellmybook.smbaudiobookcreator.desktop
	install -Dm644 data/app.sellmybook.smbaudiobookcreator.metainfo.xml $(DESTDIR)$(DATADIR)/metainfo/app.sellmybook.smbaudiobookcreator.metainfo.xml
	install -Dm644 data/app.sellmybook.smbaudiobookcreator.svg $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/app.sellmybook.smbaudiobookcreator.svg
	install -Dm644 data/app.sellmybook.smbaudiobookcreator.png $(DESTDIR)$(DATADIR)/icons/hicolor/512x512/apps/app.sellmybook.smbaudiobookcreator.png

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/smb-ab-creator
	rm -f $(DESTDIR)$(APPDIR)/*.py
	rm -f $(DESTDIR)$(APPDIR)/data/gmb-page.png
	rmdir --ignore-fail-on-non-empty $(DESTDIR)$(APPDIR)/data
	rmdir --ignore-fail-on-non-empty $(DESTDIR)$(APPDIR)
	rm -f $(DESTDIR)$(DATADIR)/applications/app.sellmybook.smbaudiobookcreator.desktop
	rm -f $(DESTDIR)$(DATADIR)/metainfo/app.sellmybook.smbaudiobookcreator.metainfo.xml
	rm -f $(DESTDIR)$(DATADIR)/icons/hicolor/scalable/apps/app.sellmybook.smbaudiobookcreator.svg
	rm -f $(DESTDIR)$(DATADIR)/icons/hicolor/512x512/apps/app.sellmybook.smbaudiobookcreator.png
