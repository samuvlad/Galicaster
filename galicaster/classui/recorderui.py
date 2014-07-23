# -*- coding:utf-8 -*-
# Galicaster, Multistream Recorder and Player
#
#       galicaster/classui/recorderui
#
# Copyright (c) 2011, Teltek Video Research <galicaster@teltek.es>
#
# This work is licensed under the Creative Commons Attribution-
# NonCommercial-ShareAlike 3.0 Unported License. To view a copy of 
# this license, visit http://creativecommons.org/licenses/by-nc-sa/3.0/ 
# or send a letter to Creative Commons, 171 Second Street, Suite 300, 
# San Francisco, California, 94105, USA.
"""
Recording Area GUI

TODO:
 * check_status_area timeout???
 * Si se quita de pausado (termina una grabacion agendada mientras está pausado) quita el pause
        elif state == GC_STOP:
            if self.previous == GC_PAUSED:
                self.pause_dialog.destroy()
 * Waiting vs Iddle en status
     if self.next == None and state == GC_PREVIEW:
            self.view.set_displayed_row(GC_PRE2)


"""

import gobject
import gtk
import gtk.glade
import pango
import datetime

from galicaster.core import context

from galicaster.classui.metadata import MetadataClass as Metadata
from galicaster.classui.audiobar import Vumeter
from galicaster.classui.events import EventManager
from galicaster.classui.about import GCAboutDialog
from galicaster.classui import message
from galicaster.classui import get_ui_path, get_image_path
from galicaster.utils import series
from galicaster.utils import readable
from galicaster.utils.resize import relabel
from galicaster.utils.i18n import _

from galicaster.recorder.service import INIT_STATUS
from galicaster.recorder.service import PREVIEW_STATUS
from galicaster.recorder.service import RECORDING_STATUS
from galicaster.recorder.service import PAUSED_STATUS
from galicaster.recorder.service import ERROR_STATUS


gtk.gdk.threads_init()

logger = context.get_logger()

# No-op function for i18n
def N_(string): return string

STATUS = [  [N_("Initialization"),"#F7F6F6"],
            [N_("Waiting"),"#F7F6F6"],
            [N_("Recording"),"#FF0000"],
            [N_("Paused"),"#F7F6F6"],
            [N_("Error"),"#FF0000"],
            ]


TIME_BLINK_START = 20
TIME_BLINK_STOP = 20
TIME_RED_START = 50
TIME_RED_STOP = 50
TIME_UPCOMING = 60

NEXT_TEXT = _("Upcoming")
CURRENT_TEXT = _("Current")


class RecorderClassUI(gtk.Box):
    """
    Graphic User Interface for Record alone
    """

    __gtype_name__ = 'RecorderClass'

    def __init__(self, package=None): 
  
        logger.info("Creating Recording Area")
        gtk.Box.__init__(self)
	builder = gtk.Builder()
        builder.add_from_file(get_ui_path('recorder.glade'))
       
        self.repo = context.get_repository()
        self.dispatcher = context.get_dispatcher()
        self.worker = context.get_worker()
        self.conf = context.get_conf()
        self.recorder = context.get_recorder()
        self.recorder.set_create_drawing_areas_func(self.create_drawing_areas)
        self.start_recording = False
        self.font = None
        self.scheduled_recording = False
        self.focus_is_active = False
        self.net_activity = None

        self.error_dialog = None

        # BUILD
        self.recorderui = builder.get_object("recorderbox")
        self.main_area = builder.get_object("videobox")
        self.vubox = builder.get_object("vubox")
        self.gui = builder

        # SWAP
        if not self.conf.get_boolean('basic', 'swapvideos'):
            self.gui.get_object("swapbutton").hide()
        self.swap = False

        # STATUS
        big_status = builder.get_object("bg_status")
        self.view = self.set_status_view()
        big_status.add(self.view)
        self.dispatcher.connect("galicaster-init", self.check_status_area)
        self.dispatcher.connect("galicaster-init", self.check_net)
        self.dispatcher.connect("net-up", self.check_net, True)        
        self.dispatcher.connect("net-down", self.check_net, False)        

        # VUMETER
        self.audiobar=Vumeter()

        # UI
        self.vubox.add(self.audiobar)
        self.pack_start(self.recorderui,True,True,0)

        # Event Manager       
        self.dispatcher.connect("recorder-vumeter", self.audiobar.SetVumeter)
        self.dispatcher.connect("galicaster-status", self.event_change_mode)
        self.dispatcher.connect("recorder-status", self.handle_status)

        nb=builder.get_object("data_panel")
        pages = nb.get_n_pages()        
        for index in range(pages):
            page=nb.get_nth_page(index)
            nb.set_tab_label_packing(page, True, True,gtk.PACK_START)

        # STATES
        self.previous = None

        # PERMISSIONS
        self.allow_pause = self.conf.get_permission("pause")
        self.allow_start = self.conf.get_permission("start")
        self.allow_stop = self.conf.get_permission("stop")
        self.allow_manual = self.conf.get_permission("manual")
        self.allow_overlap = self.conf.get_permission("overlap")
     
        # OTHER
        builder.connect_signals(self)
        self.net_activity = self.conf.get_boolean('ingest', 'active')

        self.proportion = 1

        #TIMEOUTS
        deps = self.update_scheduler_deps()
        gobject.timeout_add(500, self.update_scheduler_timeout, *deps)
        self.update_clock_timeout(self.gui.get_object("local_clock"))
        gobject.timeout_add(10000, self.update_clock_timeout, self.gui.get_object("local_clock"))


    def swap_videos(self, button=None):
        """GUI callback"""
        self.swap = not self.swap
        self.dispatcher.emit("reload-profile")
        self.audiobar.mute = False        


    def on_rec(self,button=None): 
        """GUI callback for manual recording"""
        logger.info("Recording")
        self.dispatcher.emit("starting-record")
        self.recorder.record()


    def on_pause(self, button):
        """GUI callback for pause/resume the recording"""
        if self.recorder.status == PAUSED_STATUS:
            self.dispatcher.emit("enable-no-audio")
            logger.debug("Resuming Recording")
            self.recorder.resume()

        elif self.recorder.status == RECORDING_STATUS:
            self.dispatcher.emit("disable-no-audio")
            logger.debug("Pausing Recording")
            self.recorder.pause()

            self.pause_dialog = self.create_pause_dialog(self.get_toplevel())    
            if self.pause_dialog.run() == 1:
                self.on_pause(None)
            self.pause_dialog.destroy()     


    def create_pause_dialog(self, parent):
        gui = gtk.Builder()
        gui.add_from_file(get_ui_path("paused.glade"))
        dialog = gui.get_object("dialog") 
        dialog.set_transient_for(parent)
        dialog.set_type_hint(gtk.gdk.WINDOW_TYPE_HINT_TOOLBAR)
        dialog.set_modal(True)
        dialog.set_keep_above(False)
        dialog.set_skip_taskbar_hint(True)
        size = context.get_mainwindow().get_size()
        k2 = size[1] / 1080.0
        size = int(k2*150)
        dialog.set_default_size(size,size)
        button = gui.get_object("image")
        pixbuf = gtk.gdk.pixbuf_new_from_file(get_image_path('gc-pause.svg'))
        pixbuf = pixbuf.scale_simple(size, size, gtk.gdk.INTERP_BILINEAR)
        button.set_from_pixbuf(pixbuf)
        return dialog

            
    def on_ask_stop(self,button):
        """GUI callback for stops preview or recording and closes the Mediapakage"""
        if self.conf.get_boolean("basic", "stopdialog"):
            text = {"title" : _("Recorder"),
                    "main" : _("Are you sure you want to\nstop the recording?")}
            buttons = (gtk.STOCK_STOP, gtk.RESPONSE_OK, gtk.STOCK_CANCEL, gtk.RESPONSE_REJECT)
            self.dispatcher.emit("disable-no-audio")
            warning = message.PopUp(message.WARNING, text,
              context.get_mainwindow(), buttons)
            self.dispatcher.emit("enable-no-audio")
            if warning.response not in message.POSITIVE:
                return False
        self.recorder.stop()


    def on_help(self,button):
        """GUI callback to triggers a pop-up when Help button is clicked"""
        logger.info("Help requested")   

        text = {"title" : _("Help"),
                "main" : _(" Visit galicaster.teltek.es"),
                "text" : _(" ...or contact us on our community list.")
		}
        buttons = None
        self.dispatcher.emit("disable-no-audio")
        message.PopUp(message.INFO, text,
                      context.get_mainwindow(), buttons)
        self.dispatcher.emit("enable-no-audio")


    def launch_error_message(self, error_msg=None):
        """Shows an active error message."""
        msg = error_msg or self.recorder.error_msg
        text = {
            "title" : _("Recorder"),
            "main" : _(" Please review your configuration \nor load another profile"),                
            "text" : msg
			}
        self.error_dialog = message.PopUp(message.ERROR, text, 
                                context.get_mainwindow(), None)



    def destroy_error_dialog(self):
        if self.error_dialog:
            self.error_dialog.dialog_destroy()
            self.error_dialog = None
        

    def recording_info_timeout(self, rec_title, rec_elapsed):
        """gobject.timeout callback with 500 ms intervals"""
        if self.recorder.status == RECORDING_STATUS:
            if rec_title.get_text() != self.recorder.current_mediapackage.getTitle():
                rec_title.set_text(self.recorder.current_mediapackage.getTitle())
            msec = datetime.timedelta(microseconds=(self.recorder.get_recorded_time()/1000))
            rec_elapsed.set_text(_("Elapsed Time: ") + readable.long_time(msec))
            return True
        return False


    def update_clock_timeout(self, clock):
        """gobject.timeout callback with 10000 ms intervals"""
        clocktime = datetime.datetime.now().time().strftime("%H:%M")
        clock.set_text(clocktime)
        return True


    def update_scheduler_deps(self):
        """dependences for gobject.timeout callback with 500 ms intervals"""
        event_type = self.gui.get_object("nextlabel")
        title = self.gui.get_object("titlelabel")
        status = self.gui.get_object("eventlabel")

        # Status panel
        # status_disk = self.gui.get_object("status1")
        # status_hours = self.gui.get_object("status2")
        # status_mh = self.gui.get_object("status3")
        parpadeo = True
        changed = False
        
        if self.font == None:
            anchura = self.get_toplevel().get_screen().get_width()
            if anchura not in [1024,1280,1920]:
                anchura = 1920            
            k1 = anchura / 1920.0
            self.font = pango.FontDescription("bold "+str(k1*42))
        
        bold = pango.AttrWeight(700, 0, -1)
        red = pango.AttrForeground(65535, 0, 0, 0, -1)        
        black = pango.AttrForeground(17753, 17753, 17753, 0, -1)
        font = pango.AttrFontDesc(self.font, 0, -1)

        attr_red = pango.AttrList()
        attr_black = pango.AttrList()

        attr_red.insert(red)
        attr_red.insert(font)
        attr_red.insert(bold)

        attr_black.insert(black)
        attr_black.insert(font)
        attr_black.insert(bold)

        status.set_attributes(attr_black)
        
        return status, event_type, title, attr_red, attr_black, changed, parpadeo


    def update_scheduler_timeout(self, status, event_type, title, attr_red, attr_black, changed, parpadeo):
        """gobject.timeout callback with 500 ms intervals"""
        if self.recorder.current_mediapackage and not self.recorder.current_mediapackage.manual:
            start = self.recorder.current_mediapackage.getLocalDate()
            duration = self.recorder.current_mediapackage.getDuration() / 1000
            end = start + datetime.timedelta(seconds=duration)
            dif = end - datetime.datetime.now()
            status.set_text(_("Stopping in {0}").format(readable.long_time(dif)))
            event_type.set_text(CURRENT_TEXT) 
            title.set_text(self.recorder.current_mediapackage.title)             
                
            if dif < datetime.timedelta(0, TIME_RED_STOP):
                if not changed:
                    status.set_attributes(attr_red)
                    changed = True
            elif changed:
                status.set_attributes(attr_black)
                changed = False
            if dif < datetime.timedelta(0,TIME_BLINK_STOP):
                parpadeo = not parpadeo

        else:
            next_mediapackage = self.repo.get_next_mediapackage()
            if next_mediapackage:
                start = next_mediapackage.getLocalDate()
                dif = start - datetime.datetime.now()
                if event_type.get_text != NEXT_TEXT:
                    event_type.set_text(NEXT_TEXT)
                if title.get_text() != next_mediapackage.title:
                    title.set_text(next_mediapackage.title)
                status.set_text(_("Starting in {0}").format(readable.long_time(dif)))

                if dif < datetime.timedelta(0,TIME_RED_START):
                    if not changed:
                        status.set_attributes(attr_red)
                        changed = True
                elif changed:
                    status.set_attributes(attr_black)
                    changed = False

                if dif < datetime.timedelta(0,TIME_BLINK_START):
                    if parpadeo:
                        status.set_text("")
                        parpadeo =  False
                    else:
                        parpadeo = True

            else: # Not current or pending recordings
                if event_type.get_text():                
                    event_type.set_text("")
                if status.get_text():
                    status.set_text("")
                if title.get_text() != _("No upcoming events"):
                    title.set_text(_("No upcoming events"))

        return True
    


    def on_edit_meta(self,button):
        """GUI callback Pops up the  Metadata editor of the active Mediapackage"""
        self.dispatcher.emit("disable-no-audio")
        if self.recorder.current_mediapackage and self.recorder.current_mediapackage.manual:
            Metadata(self.recorder.current_mediapackage, series.get_series(), parent=self)
            self.dispatcher.emit("enable-no-audio")
        return True 


    def show_next(self,button=None,tipe = None):   
        """GUI callback Pops up the Event Manager"""
        self.dispatcher.emit("disable-no-audio")
        EventManager()
        self.dispatcher.emit("enable-no-audio")
        return True


    def show_about(self,button=None,tipe = None):
        """GUI callback Pops up de About Dialgo"""
        about_dialog = GCAboutDialog()
        self.dispatcher.emit("disable-no-audio")
        about_dialog.set_transient_for(context.get_mainwindow())
        about_dialog.set_modal(True)
        about_dialog.set_keep_above(False)
        about_dialog.show()
        about_dialog.connect('response', self.on_about_dialog_response)

    
    def on_about_dialog_response(self, origin, response_id):
        if response_id == gtk.RESPONSE_CLOSE or response_id == gtk.RESPONSE_CANCEL:
            self.dispatcher.emit("enable-no-audio") 
            origin.hide()


    def create_drawing_areas(self, sources):
        """Create as preview areas as video sources exits"""
        main = self.main_area

        for child in main.get_children():
            main.remove(child)
            child.destroy()        

        if self.swap:
            sources.reverse()

        areas = dict()
        for source in sources:
            new_area = gtk.DrawingArea()
            new_area.set_name(source)
            new_area.modify_bg(gtk.STATE_NORMAL, gtk.gdk.color_parse("black"))
            areas[source] = new_area
            main.pack_start(new_area, True, True, int(self.proportion*3))

        for child in main.get_children():
            child.show()
         
        return areas

    def event_change_mode(self, orig, old_state, new_state):
        """Handles the focus or the Rercording Area, launching messages when focus is recoverde"""
        if new_state == 0: 
            self.focus_is_active = True
            self.recorder.mute_preview(False)
            if self.recorder.status == ERROR_STATUS: 
                self.launch_error_message()

        if old_state == 0:
            self.focus_is_active = False
            self.recorder.mute_preview(True)


    def change_mode(self, button):
        """GUI callback Launch the signal to change to another area"""
        self.dispatcher.emit("change-mode", 3) # FIXME use constant


    def set_status_view(self):
        """Set the message and color of the status pilot on the top bar"""

        size = context.get_mainwindow().get_size()
        # k1 = size[0] / 1920.0
        k2 = size[1] / 1080.0

        l = gtk.ListStore(str,str,str)

        main_window = context.get_mainwindow()
        main_window.realize()
        style=main_window.get_style()

        bgcolor = style.bg[gtk.STATE_PRELIGHT]  
        fgcolor = style.fg[gtk.STATE_PRELIGHT]  

        for i in STATUS:
            if i[0] in ["Recording", "Error"]:
                l.append([_(i[0]), i[1], fgcolor])
            else:            
                l.append([_(i[0]), bgcolor, fgcolor])

        v = gtk.CellView()
        v.set_model(l)


        r = gtk.CellRendererText()
        self.renderer=r
        r.set_alignment(0.5,0.5)
        r.set_fixed_size(int(k2*400),-1)


        # k1 = size[0] / 1920.0
        k2 = size[1] / 1080.0
        font = pango.FontDescription("bold "+ str(int(k2*48)))
        r.set_property('font-desc', font)
        v.pack_start(r,True)
        v.add_attribute(r, "text", 0)
        v.add_attribute(r, "background", 1)   
        v.add_attribute(r, "foreground", 2)   
        v.set_displayed_row(0)
        return v

    #TODO timeout
    def check_status_area(self, origin, signal=None, other=None): 
        """Updates the values on the recording tab"""
        s1 = self.gui.get_object("status1")
        s2 = self.gui.get_object("status2")
        # s3 = self.gui.get_object("status3")
        s4 = self.gui.get_object("status4")
 
        freespace = self.repo.get_free_space()
        text_space = readable.size(freespace)
        
        s1.set_text(text_space)
        four_gb = 4000000000.0
        hours = int(freespace/four_gb)
        s2.set_text(_("{0} hours left").format(str(hours)))
        agent = self.conf.hostname # TODO just consult it once
        if s4.get_text() != agent:
            s4.set_text(agent)


    def check_net(self, origin, status=None):
        """Update the value of the network status"""
        attr1= pango.AttrList()
        attr2= pango.AttrList()
        attr3= pango.AttrList()
        attr4= pango.AttrList()
        gray= pango.AttrForeground(20000, 20000, 20000, 0, -1)
        red = pango.AttrForeground(65535, 0, 0, 0, -1)
        green = pango.AttrForeground(0, 65535, 0, 0, -1)
        black= pango.AttrForeground(5000, 5000, 5000, 0, -1)
        attr1.insert(gray)
        attr2.insert(green)
        attr3.insert(red)
        attr4.insert(black)

        s3 = self.gui.get_object("status3")
        if not self.net_activity:
            s3.set_text("Disabled")
            s3.set_attributes(attr1)
        else:
            try:
                if status:
                    s3.set_text("Up")
                    s3.set_attributes(attr2)
                else:
                    s3.set_text("Down")  
                    s3.set_attributes(attr3)
            except KeyError:
                s3.set_text("Connecting")
                s3.set_attributes(attr4)


    def resize(self):
        """Adapts GUI elements to the screen size"""
        size = context.get_mainwindow().get_size()
        altura = size[1]
        anchura = size[0]
        
        k1 = anchura / 1920.0
        k2 = altura / 1080.0
        self.proportion = k1

        #Recorder
        clock = self.gui.get_object("local_clock")
        logo = self.gui.get_object("classlogo")       
        nextl = self.gui.get_object("nextlabel")
        title = self.gui.get_object("titlelabel")
        # eventl = self.gui.get_object("eventlabel")
        pbox = self.gui.get_object("prebox")

        rec_title = self.gui.get_object("recording1")
        rec_elapsed = self.gui.get_object("recording3")
        status_panel = self.gui.get_object('status_panel')

        l1 = self.gui.get_object("tab1")
        l2 = self.gui.get_object("tab2")
        l3 = self.gui.get_object("tab3")
                    
        relabel(clock,k1*25,False)
        font = pango.FontDescription("bold "+str(int(k2*48)))
        self.renderer.set_property('font-desc', font)
        self.renderer.set_fixed_size(int(k2*400),-1)
        pixbuf = gtk.gdk.pixbuf_new_from_file(get_image_path('logo.svg'))  
        pixbuf = pixbuf.scale_simple(
            int(pixbuf.get_width()*k1),
            int(pixbuf.get_height()*k1),
            gtk.gdk.INTERP_BILINEAR)
        logo.set_from_pixbuf(pixbuf)

        modification = "bold "+str(k1*42)
        self.font = pango.FontDescription(modification)     
        relabel(nextl,k1*25,True)
        relabel(title,k1*33,True)

        # REC AND STATUS PANEL
        relabel(rec_title, k1*25, True)
        rec_title.set_line_wrap(True)
        rec_title.set_width_chars(40)
        relabel(rec_elapsed, k1*28, True)

        for child in status_panel.get_children():
            if type(child) is gtk.Label:
                relabel(child,k1*19,True)
        relabel(l1,k1*20,False)
        relabel(l2,k1*20,False)
        relabel(l3,k1*20,False)

        for name  in ["recbutton","pausebutton","stopbutton","editbutton","swapbutton","helpbutton"]:
            button = self.gui.get_object(name)
            button.set_property("width-request", int(k1*100) )
            button.set_property("height-request", int(k1*100) )

            image = button.get_children()
            if type(image[0]) == gtk.Image:
                image[0].set_pixel_size(int(k1*80))   
            elif type(image[0]) == gtk.VBox:
                for element in image[0].get_children():
                    if type(element) == gtk.Image:
                        element.set_pixel_size(int(k1*46))
            else:
                relabel(image[0],k1*28,False)
        # change stop button
        for name in ["pause","stop"]:
            button = self.gui.get_object(name+"button")
            image = button.get_children()[0]
            pixbuf = gtk.gdk.pixbuf_new_from_file(get_image_path('gc-'+name+'.svg'))
            pixbuf = pixbuf.scale_simple(
                int(80*k1),
                int(80*k1),
                gtk.gdk.INTERP_BILINEAR)
            image.set_from_pixbuf(pixbuf)  

        for name  in ["previousbutton", "morebutton"]:
            button = self.gui.get_object(name)
            button.set_property("width-request", int(k1*70) )
            button.set_property("height-request", int(k1*70) )

            image = button.get_children()
            if type(image[0]) == gtk.Image:
                image[0].set_pixel_size(int(k1*56))  


        talign = self.gui.get_object("top_align")
        talign.set_padding(int(k1*10),int(k1*25),0,0)
        calign = self.gui.get_object("control_align")
        calign.set_padding(int(k1*10),int(k1*30),int(k1*50),int(k1*50))
        vum = self.gui.get_object("vubox")
        vum.set_padding(int(k1*20),int(k1*10),int(k1*40),int(k1*40))         
        pbox.set_property("width-request", int(k1*225) )        
        return True

        
    def handle_status(self, origin, status):
        """Activates or deactivates the buttons depending on the new status"""
        print status

        record = self.gui.get_object("recbutton")
        pause = self.gui.get_object("pausebutton")
        stop = self.gui.get_object("stopbutton")
        helpb = self.gui.get_object("helpbutton")
        editb = self.gui.get_object("editbutton")
        prevb = self.gui.get_object("previousbutton")
        swapb = self.gui.get_object("swapbutton")

        if status == INIT_STATUS:
            record.set_sensitive(False)
            pause.set_sensitive(False)
            stop.set_sensitive(False)
            helpb.set_sensitive(True)
            prevb.set_sensitive(True)
            editb.set_sensitive(False)
            swapb.set_sensitive(False)
            self.view.set_displayed_row(0)

        elif status == PREVIEW_STATUS:
            record.set_sensitive( (self.allow_start or self.allow_manual) )
            pause.set_sensitive(False)
            pause.set_active(False)
            stop.set_sensitive(False)
            helpb.set_sensitive(True)
            prevb.set_sensitive(True)
            editb.set_sensitive(False)
            swapb.set_sensitive(True)
            self.view.set_displayed_row(1)

        elif status == RECORDING_STATUS:
            gobject.timeout_add(500, self.recording_info_timeout, 
                                self.gui.get_object("recording1"), 
                                self.gui.get_object("recording3"))

            record.set_sensitive(False)
            pause.set_sensitive(self.allow_pause and self.recorder.is_pausable()) 
            stop.set_sensitive( (self.allow_stop or self.allow_manual) )
            helpb.set_sensitive(True)
            prevb.set_sensitive(False)
            swapb.set_sensitive(False)
            editb.set_sensitive(self.recorder.current_mediapackage and self.recorder.current_mediapackage.manual)
            self.view.set_displayed_row(2)

        elif status == PAUSED_STATUS:
            record.set_sensitive(False)
            pause.set_sensitive(False) 
            stop.set_sensitive(False)
            prevb.set_sensitive(False)
            helpb.set_sensitive(False)
            editb.set_sensitive(False)
            self.view.set_displayed_row(3)

        elif status == ERROR_STATUS:
            record.set_sensitive(False)
            pause.set_sensitive(False)
            stop.set_sensitive(False)
            helpb.set_sensitive(True) 
            prevb.set_sensitive(True)
            editb.set_sensitive(False)
            self.view.set_displayed_row(4)
            if self.focus_is_active:
                self.launch_error_message()


    def block(self):
        prev = self.gui.get_object("prebox")
        prev.set_child_visible(False)
        self.focus_is_active = True
        self.recorder.mute_preview(False)

        # Show Help or Edit_meta
        helpbutton = self.gui.get_object("helpbutton")
        helpbutton.set_visible(True)
        editbutton = self.gui.get_object("editbutton")
        editbutton.set_visible(False)


gobject.type_register(RecorderClassUI)
