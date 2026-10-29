import QtQuick 2.9
import MuseScore 3.0

MuseScore {
    id: root
    menuPath: "Plugins.MuseScore API Server"
    description: "Exposes MuseScore API via WebSocket (Clean Version)"
    version: "2.0"
    
    property var clientConnections: []
    property var selectionState: ({
        startStaff: 0,
        endStaff: 1,
        startTick: 0,
        elements: []
    })

    // ========================================
    // WEBSOCKET & MESSAGE PROCESSING
    // ========================================

    function processMessage(message, clientId) {
        console.log("Received message: " + message);
        try {
            var command = JSON.parse(message);
            var result = processCommand(command);
            api.websocketserver.send(clientId, JSON.stringify({
                status: "success",
                result: result
            }));
        } catch (e) {
            console.log("Error processing command: " + e.toString());
            api.websocketserver.send(clientId, JSON.stringify({
                status: "error",
                message: e.toString()
            }));
        }
    }

    function processCommand(command) {
        console.log("Processing command: " + command.action);
        
        switch(command.action) {
            // Core operations
            case "getScore":                return getScore(command.params);
            case "syncStateToSelection":    return syncStateToSelection();
            case "ping":                    return "pong";
            case "undo":                    return undo();
            case "goToBeginningOfScore":    return goToBeginningOfScore();
            case "processSequence":         return processSequence(command.params);

            // Navigation
            case "getCursorInfo":           return getCursorInfo(command.params);
            case "goToMeasure":             return goToMeasure(command.params);
            case "goToFinalMeasure":        return goToFinalMeasure(command.params);
            case "nextElement":             return nextElement(command.params);
            case "prevElement":             return prevElement(command.params);
            case "nextStaff":               return nextStaff(command.params);
            case "prevStaff":               return prevStaff(command.params);

            // Selection
            case "selectCurrentMeasure":    return selectCurrentMeasure(command.params);
            case "selectCustomRange":       return selectCustomRange(command.params);
            case "selectMeasures":          return selectMeasuresAction(command.params);

            // Notes & Music
            case "addNote":                 return addNote(command.params);
            case "addRest":                 return addRest(command.params);
            case "addTuplet":               return addTuplet(command.params);
            case "addLyrics":               return addLyrics(command.params);

            // Measures
            case "appendMeasure":           return appendMeasure(command.params);
            case "insertMeasure":           return insertMeasure(command.params);
            case "deleteSelection":         return deleteSelection(command.params);
            case "deleteMeasures":          return deleteMeasures(command.params);

            // Staff & Instruments
            case "addInstrument":           return addInstrument(command.params);
            case "setStaffMute":            return setStaffMute(command.params);
            case "setInstrumentSound":      return setInstrumentSound(command.params);
            case "setTimeSignature":        return setTimeSignature(command.params);
            case "setTempo":                return setTempo(command.params);

            // Repeats & Formatting
            case "setRepeatBarlines":       return setRepeatBarlines(command.params);
            case "addVolta":                return addVolta(command.params);
            case "colorRange":              return colorRange(command.params);
            case "setStyle":                return setStyle(command.params);
            case "addRehearsalMark":        return addRehearsalMark(command.params);
            case "addHighlight":            return addHighlight(command.params);
            case "addLabel":                return addLabel(command.params);
            case "clearHighlights":         return clearHighlights(command.params);

            // Score files & parts
            case "scoreFileInfo":           return scoreFileInfo();
            case "saveScore":               return saveScore();
            case "openScore":               return openScore(command.params);
            case "reopenScore":             return reopenScore(command.params);
            case "removeParts":             return removeParts(command.params);

            default:
                throw new Error("Unknown command: " + command.action);
        }
    }

    // ========================================
    // UTILITY FUNCTIONS
    // ========================================

    function validateParams(params, required) {
        var missing = [];
        for (var i = 0; i < required.length; i++) {
            if (params[required[i]] === undefined) {
                missing.push(required[i]);
            }
        }
        return missing.length > 0 ? { error: "Missing required parameters: " + missing.join(", ") } : { valid: true };
    }

    // ========================================
    // MEASURE HELPERS
    // ========================================

    // Measure object for a 1-based measure number, or null when out of range.
    function getMeasure(number) {
        if (!curScore || number < 1) return null;
        var m = curScore.firstMeasure;
        for (var i = 1; m && i < number; i++) {
            m = m.nextMeasure;
        }
        return m;
    }

    function measureStartTick(m) {
        return m.firstSegment.tick;
    }

    function measureEndTick(m) {
        return m.nextMeasure ? measureStartTick(m.nextMeasure) : curScore.lastSegment.tick;
    }

    // Resolve a 1-based measure range (and optional staff range) to ticks.
    // endStaff is exclusive and defaults to the last staff.
    function measureRange(params) {
        var validation = validateParams(params, ["startMeasure"]);
        if (!validation.valid) return validation;

        var endMeasure = params.endMeasure || params.startMeasure;
        var first = getMeasure(params.startMeasure);
        var last = getMeasure(endMeasure);
        if (!first || !last || endMeasure < params.startMeasure) {
            return { error: "Invalid measure range " + params.startMeasure + "-" + endMeasure + " (score has " + curScore.nmeasures + " measures)" };
        }

        var startStaff = params.startStaff !== undefined ? params.startStaff : 0;
        var endStaff = params.endStaff !== undefined && params.endStaff !== null ? params.endStaff : curScore.nstaves;
        if (startStaff < 0 || endStaff > curScore.nstaves || startStaff >= endStaff) {
            return { error: "Invalid staff range " + startStaff + "-" + endStaff + " (score has " + curScore.nstaves + " staves, endStaff is exclusive)" };
        }

        return {
            valid: true,
            startMeasure: params.startMeasure,
            endMeasure: endMeasure,
            firstMeasure: first,
            lastMeasure: last,
            startTick: measureStartTick(first),
            endTick: measureEndTick(last),
            startStaff: startStaff,
            endStaff: endStaff
        };
    }

    // Length of one beat in ticks: the time signature's denominator (a quarter in 4/4, an eighth in 6/8).
    function beatTicks(m) {
        var sig = m.timesigActual;
        return sig && sig.denominator ? 1920 / sig.denominator : 480;
    }

    // Narrow a measure range to beats: startBeat (1-based, fractions allowed) in the first measure,
    // endBeat (inclusive, the whole beat) in the last measure. Returns an error object or the range.
    function applyBeats(range, params) {
        if (params.startBeat !== undefined && params.startBeat !== null) {
            var startOffset = (params.startBeat - 1) * beatTicks(range.firstMeasure);
            if (params.startBeat < 1 || range.startTick + startOffset >= measureEndTick(range.firstMeasure)) {
                return { error: "Beat " + params.startBeat + " is outside measure " + range.startMeasure };
            }
            range.startTick += Math.round(startOffset);
        }
        if (params.endBeat !== undefined && params.endBeat !== null) {
            var lastStart = measureStartTick(range.lastMeasure);
            var endTick = lastStart + Math.round(params.endBeat * beatTicks(range.lastMeasure));
            if (params.endBeat < 1 || endTick > range.endTick) {
                return { error: "Beat " + params.endBeat + " is outside measure " + range.endMeasure };
            }
            range.endTick = endTick;
        }
        if (range.startTick >= range.endTick) return { error: "The beat range is empty" };
        return range;
    }

    function selectMeasureRange(range) {
        curScore.selection.clear();
        curScore.selection.selectRange(range.startTick, range.endTick, range.startStaff, range.endStaff);
        selectionState = {
            startStaff: range.startStaff,
            endStaff: range.endStaff,
            startTick: range.startTick,
            elements: {},
            totalDuration: range.endTick - range.startTick
        };
    }

    function executeWithUndo(operation) {
        if (!curScore) return { error: "No score open" };
        
        curScore.startCmd();
        try {
            var result = operation();
            curScore.endCmd();
            return result;
        } catch (e) {
            curScore.endCmd(true);
            return { error: e.toString() };
        }
    }

    function getNoteName(note) {
        const noteNames = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
        return noteNames[note % 12];
    }

    function getTpcName(tpc) {
        if (tpc === -1) return "Fbb";
        var tpcNames = [
            "Cbb", "Gbb", "Dbb", "Abb", "Ebb", "Bbb", "Fb",
            "Cb",  "Gb",  "Db",  "Ab",  "Eb",  "Bb",  "F",
            "C",   "G",   "D",   "A",   "E",   "B",   "F#",
            "C#",  "G#",  "D#",  "A#",  "E#",  "B#",  "F##",
            "C##", "G##", "D##", "A##", "E##", "B##", "F###"
        ];
        if (tpc >= 0 && tpc < tpcNames.length) {
            return tpcNames[tpc];
        }
        return "Unknown";
    }

    function getDurationName(duration) {
        const durationNames = ["LONG","BREVE","WHOLE","HALF","QUARTER","EIGHTH","16TH","32ND","64TH","128TH","256TH","512TH","1024TH","ZERO","MEASURE","INVALID"];
        return durationNames[duration] || "UNKNOWN";
    }

    // ========================================
    // CURSOR MANAGEMENT
    // ========================================

    function createCursor(params) {
        if (!curScore) throw new Error("No score open");
        
        if (!params || Object.keys(params).length === 0) {
            params = selectionState;
        }
        
        var cursor = curScore.newCursor();
        cursor.inputStateMode = Cursor.INPUT_STATE_SYNC_WITH_SCORE;
        
        // Set track
        if (params.startStaff !== undefined) cursor.staffIdx = params.startStaff;
        if (params.voice !== undefined) cursor.voice = params.voice;
        
        // Position cursor
        if (params.rewindMode !== undefined) {
            cursor.rewind(params.rewindMode);
        } else if (params.startTick !== undefined) {
            try {
                cursor.rewindToTick(params.startTick);
            } catch (e) {
                console.log("rewindToTick failed, using manual navigation");
                cursor.rewind(0);
                while (cursor.tick < params.startTick && cursor.next()) {}
            }
        } else if (params.measure !== undefined) {
            cursor.rewind(0);
            for (var i = 0; i < params.measure && cursor.nextMeasure(); i++) {}
        } else {
            cursor.rewind(0);
        }
        
        // Set duration
        if (params.duration) {
            cursor.setDuration(params.duration.numerator || 1, params.duration.denominator || 4);
        }
        
        return cursor;
    }

    function initCursorState() {
        if (!curScore) return "No score open";
        
        return executeWithUndo(function() {
            var cursor = curScore.newCursor();
            cursor.rewind(0);

            var startTick = cursor.tick;
            cursor.next();
            var endTick = cursor.tick;
            var element = cursor.element;

            selectionState = {
                startStaff: cursor.staffIdx,
                endStaff: cursor.staffIdx + 1,
                startTick: startTick,
                elements: element ? [processElement(element)] : []
            };
            
            curScore.selection.clear();
            curScore.selection.selectRange(startTick, endTick, 0, 0);
            
            return "Initialized at " + [startTick, endTick, 0, 0].join(',');
        });
    }

    // ========================================
    // ELEMENT PROCESSING
    // ========================================

    function processElement(element) {
        if (!element) return null;
        if (element.name === "MeasureRepeat") {
            return {
                name: element.name,
                numMeasures: element.subtype || 1,
                durationTicks: element.actualDuration ? element.actualDuration.ticks : 0
            };
        }
        if (element.name !== "Chord" && element.name !== "Rest") return null;

        var base = {
            name: element.name,
            durationTicks: element.actualDuration ? element.actualDuration.ticks : 0,
            isTie: element.tieForward ? true : false,
            isTuplet: element.tuplet ? true : false
        };

        if (element.lyrics && element.lyrics.length > 0) {
            base.lyrics = [];
            for (var l = 0; l < element.lyrics.length; l++) {
                var lyr = element.lyrics[l];
                if (lyr) {
                    base.lyrics.push({
                        text: lyr.text,
                        no: lyr.no,
                        syllabic: lyr.syllabic
                    });
                }
            }
        }

        if (element.name === "Chord") {
            base.notes = [];
            var notesObj = element.notes || {};
            var keys = Object.keys(notesObj);
            for (var k = 0; k < keys.length; k++) {
                var note = notesObj[keys[k]];
                base.notes.push({
                    pitchMidi: note.pitch,
                    tpc: note.tpc,
                    pitchName: getTpcName(note.tpc)
                });
            }
        }
                
        return base;
    }

    // ========================================
    // CORE OPERATIONS
    // ========================================

    function undo() {
        return executeWithUndo(function() {
            cmd("undo");
            return { success: true, message: "Undo successful" };
        });
    }

    function goToBeginningOfScore() {
        var response = initCursorState();
        return { 
            success: true, 
            message: response, 
            currentSelection: selectionState,
            currentScore: getScoreSummary({ includeElements: false })
        };
    }

    function processSequence(params) {
        if (!curScore) return { error: "No score open" };
        if (!params.sequence) return { error: "No sequence specified" };

        var validCommands = [
            "getScore", "addNote", "addRest", "addTuplet", "appendMeasure", "deleteSelection",
            "getCursorInfo", "goToMeasure", "nextElement", "prevElement", "nextStaff", "prevStaff",
            "selectCurrentMeasure", "processSequence", "insertMeasure", "goToFinalMeasure",
            "goToBeginningOfScore", "setTimeSignature", "addLyrics", "addInstrument",
            "setStaffMute", "setInstrumentSound", "setTempo",
            "selectMeasures", "setRepeatBarlines", "addVolta", "colorRange", "deleteMeasures",
            "setStyle", "addRehearsalMark", "removeParts", "addHighlight", "addLabel", "clearHighlights"
        ];

        try {
            for (var i = 0; i < params.sequence.length; i++) {
                var command = params.sequence[i];
                if (!validCommands.includes(command.action)) {
                    throw new Error("Invalid command: " + command.action);
                }
                processCommand(command);
            }
            return { success: true, message: "Sequence processed", currentSelection: selectionState };
        } catch (e) {
            return { error: e.toString() };
        }
    }

    // ========================================
    // NAVIGATION FUNCTIONS
    // ========================================

    function syncStateToSelection() {
        if (!curScore) return { error: "No score open" };

        try {
            var selection = curScore.selection;
            var startSegment = selection.startSegment;
            var endSegment = selection.endSegment;

            if (startSegment && endSegment) {
                var cursor = createCursor({
                    startTick: startSegment.tick,
                    startStaff: selection.startStaff    
                });

                var elementsMap = {};
                for (var st = selection.startStaff; st < selection.endStaff; st++) {
                    elementsMap[`staff${st}`] = [];
                }

                var currentSegment = startSegment;
                while (currentSegment && currentSegment.tick < endSegment.tick) {
                    for (var s = selection.startStaff; s < selection.endStaff; s++) {
                        for (var v = 0; v < 4; v++) {
                            var track = s * 4 + v;
                            var el = currentSegment.elementAt(track);
                            if (el) {
                                var processed = processElement(el);
                                if (processed) {
                                    processed.voice = v;
                                    processed.startTick = currentSegment.tick;
                                    elementsMap[`staff${s}`].push(processed);
                                }
                            }
                        }
                    }
                    currentSegment = currentSegment.next;
                }

                selectionState = {
                    startStaff: selection.startStaff,
                    endStaff: selection.endStaff,
                    startTick: startSegment.tick,
                    elements: elementsMap,
                    totalDuration: endSegment.tick - startSegment.tick
                };
            } else {
                var c = createCursor();
                if (c && c.element) {
                    var elElement = processElement(c.element);
                    elElement.startTick = c.tick;
                    var sStart = selection.startStaff || 0;
                    var singleMap = {};
                    singleMap[`staff${sStart}`] = [elElement];
                    
                    selectionState = {
                        startStaff: sStart,
                        endStaff: sStart + 1,
                        startTick: c.tick,
                        elements: singleMap,
                        totalDuration: elElement.durationTicks
                    };
                } else {
                    return { error: "No valid selection or cursor elements found" };
                }
            }

            return { success: true, currentSelection: selectionState };
        } catch (e) {
            return { success: false, error: e.toString() };
        }
    }

    function getCursorInfo(params) {
        if (!curScore) return { error: "No score open" };
        
        syncStateToSelection();
        return { 
            success: true, 
            currentSelection: selectionState, 
            currentScore: params && params.verbose !== "false" ? getScoreSummary({ includeElements: false }) : null
        };
    }

    function goToMeasure(params) {
        var validation = validateParams(params, ["measure"]);
        if (!validation.valid) return validation;

        return executeWithUndo(function() {
            var score = getScoreSummary({ includeElements: false });
            if (params.measure < 1 || params.measure > score.measures.length) {
                return { error: "Invalid measure number" };
            }
            var measureIdx = params.measure - 1;
            var measure = score.measures[measureIdx];
            var startTick = measure.startTick;
            
            var endTick = (measureIdx + 1 < score.measures.length) ? score.measures[measureIdx + 1].startTick : curScore.lastSegment.tick;
            
            curScore.selection.clear();
            curScore.selection.selectRange(startTick, endTick, 0, curScore.nstaves);
            
            var res = syncStateToSelection();
            if (res.error) return res;
            
            return { success: true, currentSelection: selectionState };
        });
    }

    function nextElement(params) {
        return executeWithUndo(function() {
            syncStateToSelection();
            
            var cursor = createCursor({ 
                startTick: selectionState.startTick, 
                startStaff: selectionState.startStaff 
            });

            var numElements = params && params.numElements || 1;
            var success = true;
            for (var i = 0; i < numElements && success; i++) {
                success = cursor.next();
            }
            
            if (success) {
                var element = processElement(cursor.element);
                var startTick = cursor.tick;
                var staffIdx = cursor.staffIdx;
                
                // Check if we need to append a measure
                if (startTick + element.durationTicks >= curScore.lastSegment.tick) {
                    cmd("append-measure");
                }

                curScore.selection.clear();
                curScore.selection.selectRange(startTick, startTick + element.durationTicks, staffIdx, staffIdx + 1);

                selectionState = {
                    startStaff: staffIdx,
                    endStaff: staffIdx + 1,
                    startTick: startTick,
                    elements: [element],
                    totalDuration: element.durationTicks
                };
                
                return { success: true, currentSelection: selectionState };
            } else {
                return { success: false, message: "End of score reached" };
            }
        });
    }

    function prevElement(params) {
        return executeWithUndo(function() {
            syncStateToSelection();
            
            var cursor = createCursor({ 
                startTick: selectionState.startTick, 
                startStaff: selectionState.startStaff 
            });

            var endTick = cursor.tick;
            var numElements = params && params.numElements || 1;
            var success = true;
            
            for (var i = 0; i < numElements && success; i++) {
                success = cursor.prev();
            }

            if (success) {
                var element = processElement(cursor.element);
                var startTick = cursor.tick;
                var staffIdx = cursor.staffIdx;
                
                curScore.selection.clear();
                curScore.selection.selectRange(startTick, endTick, staffIdx, staffIdx + 1);

                selectionState = {
                    startStaff: staffIdx,
                    endStaff: staffIdx + 1,
                    startTick: startTick,
                    elements: [element],
                    totalDuration: endTick - startTick
                };
                
                return { success: true, currentSelection: selectionState };
            } else {
                return { success: false, message: "Beginning of score reached" };
            }
        });
    }

    function nextStaff(params) {
        return executeWithUndo(function() {
            syncStateToSelection();

            if (selectionState.endStaff >= curScore.nstaves) {
                return { success: false, message: "Already at last staff" };
            }

            var newStaff = selectionState.endStaff;
            var cursor = createCursor({ 
                startTick: selectionState.startTick, 
                startStaff: newStaff 
            });

            var element = processElement(cursor.element);
            
            curScore.selection.clear();
            curScore.selection.selectRange(
                selectionState.startTick, 
                selectionState.startTick + element.durationTicks, 
                newStaff, 
                newStaff + 1
            );

            selectionState = {
                startStaff: newStaff,
                endStaff: newStaff + 1,
                startTick: selectionState.startTick,
                elements: [element],
                totalDuration: element.durationTicks
            };

            return { success: true, currentSelection: selectionState };
        });
    }

    function prevStaff(params) {
        return executeWithUndo(function() {
            syncStateToSelection();

            if (selectionState.startStaff <= 0) {
                return { success: false, message: "Already at first staff" };
            }

            var newStaff = selectionState.startStaff - 1;
            var cursor = createCursor({ 
                startTick: selectionState.startTick, 
                startStaff: newStaff 
            });

            var element = processElement(cursor.element);
            
            curScore.selection.clear();
            curScore.selection.selectRange(
                selectionState.startTick, 
                selectionState.startTick + element.durationTicks, 
                newStaff, 
                newStaff + 1
            );

            selectionState = {
                startStaff: newStaff,
                endStaff: newStaff + 1,
                startTick: selectionState.startTick,
                elements: [element],
                totalDuration: element.durationTicks
            };

            return { success: true, currentSelection: selectionState };
        });
    }

    function goToFinalMeasure(params) {
        return executeWithUndo(function() {
            var cursor = createCursor({ startTick: 0 });
            var count = 0;
            var startTick = 0;

            while (cursor.nextMeasure()) {
                startTick = cursor.tick;
                count++;
            }

            if (count === 0) {
                return { success: false, message: "Already at the last measure" };
            }

            cursor.rewindToTick(startTick);
            cursor.next();
            var endTick = cursor.tick;
            var staffIdx = cursor.staffIdx;
            
            curScore.selection.clear();
            curScore.selection.selectRange(startTick, endTick, staffIdx, staffIdx + 1);
            
            selectionState = {
                startStaff: staffIdx,
                endStaff: staffIdx + 1,
                startTick: startTick,
                elements: [processElement(cursor.element)],
                totalDuration: endTick - startTick
            };

            return { success: true, currentSelection: selectionState };
        });
    }

    // ========================================
    // SELECTION FUNCTIONS
    // ========================================

    function selectCurrentMeasure() {
        return executeWithUndo(function() {
            var cursor = createCursor({ 
                startTick: selectionState.startTick || 0, 
                startStaff: selectionState.startStaff || 0 
            });

            var currTick = cursor.tick;
            var scoreSummary = getScoreSummary({ includeElements: false });

            var measureIdx = scoreSummary.measures.filter(function(m) { 
                return m.startTick <= currTick; 
            }).length - 1;
            
            if (measureIdx < 0) return { error: "Invalid cursor position" };
            
            var measure = scoreSummary.measures[measureIdx];
            var startTick = measure.startTick;
            var endTick = (measureIdx + 1 < scoreSummary.measures.length) ? scoreSummary.measures[measureIdx + 1].startTick : curScore.lastSegment.tick;

            curScore.selection.clear();
            curScore.selection.selectRange(startTick, endTick, 0, curScore.nstaves);

            var res = syncStateToSelection();
            if (res.error) return res;
            
            return { success: true, message: `Selected measure ${measureIdx + 1}`, currentSelection: selectionState };
        });
    }

    function selectCustomRange(params) {
        var validation = validateParams(params, ["startTick", "endTick", "startStaff", "endStaff"]);
        if (!validation.valid) return validation;

        return executeWithUndo(function() {
            var startTick = params.startTick;
            var endTick = params.endTick;
            var startStaff = params.startStaff;
            var endStaff = params.endStaff;

            // Visual GUI snap
            curScore.selection.clear();
            curScore.selection.selectRange(startTick, endTick, startStaff, endStaff);

            var elementsMap = {};
            for (var st = startStaff; st <= endStaff; st++) {
                elementsMap[`staff${st}`] = [];
            }

            var c = createCursor({ startTick: 0, startStaff: startStaff });
            c.rewind(0);
            var currentSegment = c.segment;

            while (currentSegment && currentSegment.tick < startTick) {
                currentSegment = currentSegment.next;
            }

            while (currentSegment && currentSegment.tick < endTick) {
                for (var s = startStaff; s <= endStaff; s++) {
                    for (var v = 0; v < 4; v++) {
                        var track = s * 4 + v;
                        var el = currentSegment.elementAt(track);
                        if (el) {
                            var processed = processElement(el);
                            if (processed) {
                                processed.voice = v;
                                processed.startTick = currentSegment.tick;
                                elementsMap[`staff${s}`].push(processed);
                            }
                        }
                    }
                }
                currentSegment = currentSegment.next;
            }

            selectionState = {
                startStaff: startStaff,
                endStaff: endStaff,
                startTick: startTick,
                elements: elementsMap,
                totalDuration: endTick - startTick
            };

            return { success: true, message: "Custom range mapped", currentSelection: selectionState };
        });
    }

    // ========================================
    // NOTE & MUSIC OPERATIONS
    // ========================================

    function addNote(params) {
        var validation = validateParams(params, ["pitch", "duration", "advanceCursorAfterAction"]);
        if (!validation.valid) return validation;

        if (!params.duration.numerator || !params.duration.denominator) {
            return { error: "Duration must be specified as { numerator: int, denominator: int }" };
        }

        return executeWithUndo(function() {
            syncStateToSelection();
            
            var cursor = createCursor();
            cursor.setDuration(params.duration.numerator, params.duration.denominator);
            
            // Melody is the default. Pass addToChord: true to stack a pitch on the current chord.
            cursor.addNote(params.pitch, params.addToChord === true);
            cursor.rewindToTick(selectionState.startTick);

            if (params.advanceCursorAfterAction) {
                cursor.next();
            }

            var element = processElement(cursor.element);
            var startTick = cursor.tick;
            var staffIdx = cursor.staffIdx;
            var durationTicks = element && element.durationTicks ? element.durationTicks : 0;

            curScore.selection.clear();
            if (durationTicks > 0) {
                curScore.selection.selectRange(startTick, startTick + durationTicks, staffIdx, staffIdx + 1);
            }

            var syncRes = syncStateToSelection();
            if (syncRes && syncRes.error) {
                var staffMap = {};
                staffMap["staff" + staffIdx] = element ? [element] : [];
                selectionState = {
                    startStaff: staffIdx,
                    endStaff: staffIdx + 1,
                    startTick: startTick,
                    elements: staffMap,
                    totalDuration: durationTicks
                };
            }

            return { 
                success: true, 
                message: "Note added with pitch " + params.pitch,
                currentSelection: selectionState
            };
        });
    }

    function addRest(params) {
        var validation = validateParams(params, ["duration", "advanceCursorAfterAction"]);
        if (!validation.valid) return validation;

        if (!params.duration.numerator || !params.duration.denominator) {
            return { error: "Duration must be specified as { numerator: int, denominator: int }" };
        }

        return executeWithUndo(function() {
            syncStateToSelection();
            
            var cursor = createCursor();
            cursor.setDuration(params.duration.numerator, params.duration.denominator);
            cursor.addRest();
            cursor.rewindToTick(selectionState.startTick);

            if (params.advanceCursorAfterAction) {
                cursor.next();
            }

            var element = processElement(cursor.element);
            var startTick = cursor.tick;
            var staffIdx = cursor.staffIdx;

            curScore.selection.clear();
            curScore.selection.selectRange(startTick, startTick + element.durationTicks, staffIdx, staffIdx + 1);

            selectionState = {
                startStaff: staffIdx,
                endStaff: staffIdx + 1,
                startTick: startTick,
                elements: [element],
                totalDuration: element.durationTicks
            };

            return { success: true, message: "Rest added", currentSelection: selectionState };
        });
    }

    function addTuplet(params) {
        var validation = validateParams(params, ["ratio", "duration", "advanceCursorAfterAction"]);
        if (!validation.valid) return validation;

        if (!params.ratio.numerator || !params.ratio.denominator || 
            !params.duration.numerator || !params.duration.denominator) {
            return { error: "Ratio and duration must be specified as { numerator: int, denominator: int }" };
        }
        
        return executeWithUndo(function() {
            var cursor = createCursor();
            cursor.setDuration(params.duration.numerator, params.duration.denominator);
            
            var ratio = fraction(params.ratio.numerator, params.ratio.denominator);
            var duration = fraction(params.duration.numerator, params.duration.denominator);
            
            cursor.addTuplet(ratio, duration);
            cursor.next();

            if (params.advanceCursorAfterAction) {
                cursor.next();
            }

            var element = processElement(cursor.element);
            var startTick = cursor.tick;
            var staffIdx = cursor.staffIdx;

            selectionState = {
                startStaff: staffIdx,
                endStaff: staffIdx + 1,
                startTick: startTick,
                elements: [element],
                totalDuration: element.durationTicks
            };

            return { 
                success: true, 
                message: "Tuplet " + params.ratio.numerator + ":" + params.ratio.denominator + " added",
                currentSelection: selectionState
            };
        });
    }

    function addLyrics(params) {
        if (!params.lyrics || !Array.isArray(params.lyrics) || params.lyrics.length === 0) {
            return { error: "Lyrics must be specified as an array of strings" };
        }
        
        return executeWithUndo(function() {
            syncStateToSelection();
            
            var cursor = createCursor({ 
                startTick: selectionState.startTick, 
                startStaff: selectionState.startStaff 
            });
            
            var lyricsArray = params.lyrics.slice();
            var verse = params.verse || 0;
            var addedCount = 0;
            var skippedCount = 0;
            
            while (cursor.element && lyricsArray.length > 0) {
                var element = cursor.element;
                
                if (element.type === Element.CHORD || element.name === "Chord") {
                    var lyr = newElement(Element.LYRICS);
                    lyr.text = lyricsArray.shift();
                    lyr.verse = verse;
                    
                    cursor.add(lyr);
                    addedCount++;
                } else if (element.type === Element.REST || element.name === "Rest") {
                    skippedCount++;
                }
                
                if (!cursor.next()) break;
            }
            
            var finalElement = processElement(cursor.element) || selectionState.elements[0];
            var finalTick = cursor.tick;
            var staffIdx = cursor.staffIdx;
            
            selectionState = {
                startStaff: staffIdx,
                endStaff: staffIdx + 1,
                startTick: finalTick,
                elements: [finalElement],
                totalDuration: finalElement.durationTicks || selectionState.totalDuration
            };
            
            curScore.selection.clear();
            curScore.selection.selectRange(finalTick, finalTick + (finalElement.durationTicks || 0), staffIdx, staffIdx + 1);
            
            var message = `Added ${addedCount} lyrics`;
            if (skippedCount > 0) message += `, skipped ${skippedCount} rests`;
            if (lyricsArray.length > 0) message += `, ${lyricsArray.length} lyrics remaining`;
            
            return { 
                success: true, 
                message: message,
                addedCount: addedCount,
                skippedCount: skippedCount,
                remainingLyrics: lyricsArray,
                currentSelection: selectionState
            };
        });
    }

    // ========================================
    // MEASURE OPERATIONS
    // ========================================

    function appendMeasure(params) {
        return executeWithUndo(function() {
            var count = params && params.count || 1;
            
            for (var i = 0; i < count; i++) {
                cmd("append-measure");
            }
            
            return { 
                success: true, 
                message: count + " measure(s) appended",
                currentSelection: selectionState
            };
        });
    }

    function insertMeasure(params) {
        return executeWithUndo(function() {
            cmd("insert-measure");
            syncStateToSelection();
            
            return { 
                success: true, 
                message: "Measure inserted",
                currentSelection: selectionState
            };
        });
    }

    function deleteSelection(params) {
        return executeWithUndo(function() {
            if (params && params.measure) {
                createCursor({ measure: params.measure });
            }
            
            cmd("delete");
            
            return { 
                success: true, 
                message: "Selection deleted",
                currentSelection: selectionState
            };
        });
    }

    // Remove whole measures (all staves). Runs MuseScore's own "Remove selected range"
    // action, which manages its own undo step, so it is not wrapped in executeWithUndo.
    function deleteMeasures(params) {
        if (!curScore) return { error: "No score open" };
        var range = measureRange({ startMeasure: params.startMeasure, endMeasure: params.endMeasure });
        if (!range.valid) return range;

        selectMeasureRange(range);
        cmd("time-delete");
        curScore.selection.clear();
        selectionState = { startStaff: 0, endStaff: 1, startTick: 0, elements: {} };

        return {
            success: true,
            message: "Deleted measures " + range.startMeasure + "-" + range.endMeasure +
                     "; later measures are renumbered. Cursor reset to the start of the score.",
            numMeasures: curScore.nmeasures
        };
    }

    // ========================================
    // STAFF & INSTRUMENT OPERATIONS
    // ========================================

    function addInstrument(params) {
        var validation = validateParams(params, ["instrumentId"]);
        if (!validation.valid) return validation;
        
        return executeWithUndo(function() {
            curScore.appendPart(params.instrumentId);
            return { success: true, message: "Instrument " + params.instrumentId + " added" };
        });
    }

    function setStaffMute(params) {
        var validation = validateParams(params, ["staff"]);
        if (!validation.valid) return validation;
        
        return executeWithUndo(function() {
            var staff = curScore.staves && curScore.staves[params.staff] || 
                       (typeof curScore.staff === "function" ? curScore.staff(params.staff) : null);
            
            if (staff) {
                staff.invisible = Boolean(params.mute);
                return { success: true, message: "Staff " + (params.mute ? "muted" : "unmuted") };
            } else {
                return { error: "Staff not found" };
            }
        });
    }

    function setInstrumentSound(params) {
        var validation = validateParams(params, ["staff", "instrumentId"]);
        if (!validation.valid) return validation;
        
        return executeWithUndo(function() {
            cmd("instruments");
            return { success: true, message: "Instrument dialog opened, manual selection required" };
        });
    }

    function setTimeSignature(params) {
        var validation = validateParams(params, ["numerator", "denominator"]);
        if (!validation.valid) return validation;
        
        return executeWithUndo(function() {
            var cursor = createCursor();
            var currTick = cursor.tick;
            var currStaff = cursor.staffIdx;

            var ts = newElement(Element.TIMESIG);
            ts.timesig = fraction(params.numerator, params.denominator);
            cursor.add(ts);

            return { 
                success: true, 
                message: "Time signature set to " + params.numerator + "/" + params.denominator
            };
        });
    }

    function setTempo(params) {
        var validation = validateParams(params, ["bpm"]);
        if (!validation.valid) return validation;
        
        var cursorParams = undefined;
        if (params.measure !== undefined && params.measure !== null) {
            var range = measureRange({ startMeasure: params.measure, startStaff: 0, endStaff: 1 });
            if (!range.valid) return range;
            cursorParams = { startTick: range.startTick, startStaff: 0 };
        }

        return executeWithUndo(function() {
            var cursor = createCursor(cursorParams);

            var tempo = newElement(Element.TEMPO_TEXT);
            tempo.tempo = params.bpm / 60.0;
            tempo.text = "♩ = " + params.bpm;
            
            cursor.add(tempo);
            
            return { success: true, message: "Tempo set to " + params.bpm + " BPM" };
        });
    }

    // ========================================
    // REPEATS & FORMATTING
    // ========================================

    function selectMeasuresAction(params) {
        if (!curScore) return { error: "No score open" };
        var range = measureRange(params);
        if (!range.valid) return range;

        selectMeasureRange(range);
        return {
            success: true,
            message: "Selected measures " + range.startMeasure + "-" + range.endMeasure +
                     ", staves " + range.startStaff + "-" + (range.endStaff - 1),
            startTick: range.startTick,
            endTick: range.endTick,
            startStaff: range.startStaff,
            endStaff: range.endStaff
        };
    }

    // measure.repeatStart / repeatEnd are writable from plugins only in MuseScore 4.6+.
    // On older versions the bars are selected so the user can apply the palette items.
    function setRepeatBarlines(params) {
        if (!curScore) return { error: "No score open" };
        var range = measureRange({ startMeasure: params.startMeasure, endMeasure: params.endMeasure });
        if (!range.valid) return range;

        var playCount = params.playCount || 2;
        var clear = params.clear === true;
        var label = "measures " + range.startMeasure + "-" + range.endMeasure;

        if (typeof range.firstMeasure.repeatStart === "undefined") {
            selectMeasureRange(range);
            return {
                success: false,
                fallback: "manual",
                message: "This MuseScore version does not let plugins set repeat barlines (needs MuseScore 4.6+). " +
                         label + " are now selected: in the Repeats & jumps palette click the start repeat sign, " +
                         "then the end repeat sign." +
                         (playCount !== 2 ? " Then set the play count to " + playCount + " in the measure properties." : "")
            };
        }

        return executeWithUndo(function() {
            range.firstMeasure.repeatStart = !clear;
            range.lastMeasure.repeatEnd = !clear;
            range.lastMeasure.repeatCount = clear ? 2 : playCount;
            return {
                success: true,
                message: clear
                    ? "Repeat barlines removed from " + label
                    : "Repeat barlines set around " + label + " (played " + playCount + " times)"
            };
        });
    }

    // Experimental: plugins have no dedicated volta API, so this builds the spanner by hand
    // and adds it through Cursor.add. Falls back to selecting the bars for the palette.
    function addVolta(params) {
        if (!curScore) return { error: "No score open" };
        var range = measureRange({ startMeasure: params.startMeasure, endMeasure: params.endMeasure, startStaff: 0, endStaff: 1 });
        if (!range.valid) return range;

        var endings = params.endings && params.endings.length ? params.endings : [1];
        var text = params.text || (endings.join(", ") + ".");
        var label = "measures " + range.startMeasure + "-" + range.endMeasure;

        var result = executeWithUndo(function() {
            var volta = newElement(Element.VOLTA);
            volta.spannerTick = fraction(range.startTick, 1920);
            volta.spannerTicks = fraction(range.endTick - range.startTick, 1920);
            volta.beginText = text;
            try {
                volta.volta_ending = endings;
            } catch (e) {
                console.log("Could not set volta endings: " + e.toString());
            }

            var cursor = curScore.newCursor();
            cursor.staffIdx = 0;
            cursor.voice = 0;
            cursor.rewindToTick(range.startTick);
            cursor.add(volta);
            return { success: true };
        });

        selectMeasureRange(range);
        if (result && result.error) {
            return {
                success: false,
                fallback: "manual",
                message: "Could not add the volta from the plugin (" + result.error + "). " + label +
                         " are now selected: click the matching volta in the Repeats & jumps palette, then edit its text to '" + text + "'."
            };
        }
        return {
            success: true,
            experimental: true,
            message: "Volta '" + text + "' requested over " + label + ". This uses an experimental plugin route: " +
                     "check the score. If no volta appears, press Ctrl+Z and, with the bars still selected, " +
                     "click the volta in the Repeats & jumps palette."
        };
    }

    function setColor(element, color) {
        if (!element) return 0;
        try {
            element.color = color;
            return 1;
        } catch (e) {
            return 0;
        }
    }

    // Color a chord (notes, accidentals, dots, ties, stem, hook, beam) or a rest. Returns the number of notes/rests colored.
    function paintElement(element, color) {
        if (element.name === "Rest" || element.name === "MeasureRepeat") return setColor(element, color);
        if (element.name !== "Chord") return 0;

        var count = 0;
        var notesObj = element.notes || {};
        var keys = Object.keys(notesObj);
        for (var k = 0; k < keys.length; k++) {
            var note = notesObj[keys[k]];
            count += setColor(note, color);
            setColor(note.accidental, color);
            setColor(note.tieForward, color);
            var dots = note.dots || [];
            for (var d = 0; d < dots.length; d++) {
                setColor(dots[d], color);
            }
        }
        setColor(element.stem, color);
        setColor(element.hook, color);
        setColor(element.beam, color);
        return count;
    }

    function colorRange(params) {
        if (!curScore) return { error: "No score open" };
        var range = measureRange(params);
        if (!range.valid) return range;
        range = applyBeats(range, params);
        if (range.error) return range;

        var color = params.color || "#000000";
        var voices = params.voices && params.voices.length ? params.voices : [0, 1, 2, 3];

        return executeWithUndo(function() {
            var count = 0;
            var seg = range.firstMeasure.firstSegment;
            while (seg && seg.tick < range.startTick) {
                seg = seg.next;
            }
            while (seg && seg.tick < range.endTick) {
                for (var s = range.startStaff; s < range.endStaff; s++) {
                    for (var i = 0; i < voices.length; i++) {
                        var el = seg.elementAt(s * 4 + voices[i]);
                        if (el) count += paintElement(el, color);
                    }
                }
                seg = seg.next;
            }
            return {
                success: true,
                colored: count,
                message: "Colored " + count + " note(s)/rest(s) " + color + " in measures " +
                         range.startMeasure + "-" + range.endMeasure +
                         (params.startBeat || params.endBeat ? " (beats " + (params.startBeat || 1) + " to " + (params.endBeat || "end") + ")" : "") +
                         ", staves " + range.startStaff + "-" + (range.endStaff - 1)
            };
        });
    }

    function setStyle(params) {
        if (!curScore) return { error: "No score open" };
        var validation = validateParams(params, ["key", "value"]);
        if (!validation.valid) return validation;

        return executeWithUndo(function() {
            curScore.style.setValue(params.key, params.value);
            return { success: true, message: "Style " + params.key + " set to " + params.value };
        });
    }

    // Highlighter: a thick, semi-transparent text line drawn behind one staff's notes over a range of
    // measures; it follows the bars across system breaks. Experimental: added through Cursor.add.
    function addHighlight(params) {
        if (!curScore) return { error: "No score open" };
        var range = measureRange(params);
        if (!range.valid) return range;
        range = applyBeats(range, params);
        if (range.error) return range;

        var color = params.color || "#59FFE600";
        var width = params.width !== undefined ? params.width : 3.0;      // spatium; a 5-line staff is 4 spaces
        var offsetY = params.offsetY !== undefined ? params.offsetY : 2.0;

        return executeWithUndo(function() {
            for (var s = range.startStaff; s < range.endStaff; s++) {
                var line = newElement(Element.TEXTLINE);
                line.spannerTick = fraction(range.startTick, 1920);
                line.spannerTicks = fraction(range.endTick - range.startTick, 1920);
                line.spannerTrack2 = s * 4;
                line.lineWidth = width;
                line.lineColor = color;
                line.autoplace = false;
                line.offset = Qt.point(0, offsetY);
                line.z = 1;

                var cursor = curScore.newCursor();
                cursor.staffIdx = s;
                cursor.voice = 0;
                cursor.rewindToTick(range.startTick);
                cursor.add(line);
            }
            return {
                success: true,
                experimental: true,
                added: range.endStaff - range.startStaff,
                message: "Highlighted measures " + range.startMeasure + "-" + range.endMeasure +
                         " on staves " + range.startStaff + "-" + (range.endStaff - 1)
            };
        });
    }

    // A staff text in a colored box, e.g. a section tag.
    function addLabel(params) {
        if (!curScore) return { error: "No score open" };
        var validation = validateParams(params, ["measure", "text"]);
        if (!validation.valid) return validation;
        var staff = params.staff || 0;
        var range = measureRange({ startMeasure: params.measure, startStaff: staff, endStaff: staff + 1 });
        if (!range.valid) return range;

        return executeWithUndo(function() {
            var cursor = createCursor({ startTick: range.startTick, startStaff: staff });
            var label = newElement(Element.STAFF_TEXT);
            label.text = params.text;
            label.frameType = 1;  // square frame
            label.frameBgColor = params.background || "#FFF2A8";
            label.frameFgColor = params.border || "#000000";
            if (params.color) label.color = params.color;
            cursor.add(label);
            return { success: true, message: "Label '" + params.text + "' added at measure " + params.measure + ", staff " + staff };
        });
    }

    function isHighlightLine(spanner) {
        var color = spanner.lineColor;
        return spanner.name === "TextLine" && spanner.autoplace === false && color && color.a !== undefined && color.a < 1;
    }

    // Remove highlights (addHighlight lines) and boxed labels (addLabel texts) in a range.
    function clearHighlights(params) {
        if (!curScore) return { error: "No score open" };
        var range = measureRange(params);
        if (!range.valid) return range;
        var kinds = params.kinds && params.kinds.length ? params.kinds : ["highlights", "labels"];
        var targets = [];

        if (kinds.indexOf("highlights") !== -1) {
            var spanners = curScore.spanners || [];
            for (var i = 0; i < spanners.length; i++) {
                var sp = spanners[i];
                var tick = sp.spannerTick ? sp.spannerTick.ticks : -1;
                var staffIdx = Math.floor(sp.track / 4);
                if (tick >= range.startTick && tick < range.endTick &&
                        staffIdx >= range.startStaff && staffIdx < range.endStaff && isHighlightLine(sp)) {
                    targets.push(sp);
                }
            }
        }
        if (kinds.indexOf("labels") !== -1) {
            var seg = range.firstMeasure.firstSegment;
            while (seg && seg.tick < range.endTick) {
                var annotations = seg.annotations || [];
                for (var a = 0; a < annotations.length; a++) {
                    var ann = annotations[a];
                    var annStaff = Math.floor(ann.track / 4);
                    if (ann.name === "StaffText" && ann.frameType && annStaff >= range.startStaff && annStaff < range.endStaff) {
                        targets.push(ann);
                    }
                }
                seg = seg.next;
            }
        }

        if (targets.length === 0) return { success: true, removed: 0, message: "Nothing to remove" };
        curScore.selection.clear();
        for (var t = 0; t < targets.length; t++) {
            curScore.selection.select(targets[t], true);
        }
        cmd("delete");
        curScore.selection.clear();
        return { success: true, removed: targets.length, message: "Removed " + targets.length + " highlight(s)/label(s)" };
    }

    // Experimental, like addVolta: rehearsal marks have no dedicated API, so they go through Cursor.add.
    function addRehearsalMark(params) {
        if (!curScore) return { error: "No score open" };
        var validation = validateParams(params, ["measure", "text"]);
        if (!validation.valid) return validation;
        var range = measureRange({ startMeasure: params.measure, startStaff: 0, endStaff: 1 });
        if (!range.valid) return range;

        return executeWithUndo(function() {
            var cursor = createCursor({ startTick: range.startTick, startStaff: 0 });
            var mark = newElement(Element.REHEARSAL_MARK);
            mark.text = params.text;
            cursor.add(mark);
            return { success: true, experimental: true, message: "Rehearsal mark '" + params.text + "' added at measure " + params.measure };
        });
    }

    // ========================================
    // SCORE FILES & PARTS
    // ========================================

    // staff index -> Part, from each part's track range (4 tracks per staff, endTrack exclusive)
    function staffParts() {
        var map = {};
        var parts = curScore.parts || [];
        for (var p = 0; p < parts.length; p++) {
            var part = parts[p];
            for (var s = Math.floor(part.startTrack / 4); s < Math.ceil(part.endTrack / 4); s++) {
                map[s] = part;
            }
        }
        return map;
    }

    function isDrumPart(part) {
        if (part.hasDrumStaff) return true;
        return (part.instrumentId || "").indexOf("drum.") === 0;
    }

    function scoreFileInfo() {
        if (!curScore) return { error: "No score open" };
        return {
            success: true,
            scoreName: curScore.scoreName || "",
            title: curScore.title || "",
            numMeasures: curScore.nmeasures,
            numStaves: curScore.nstaves
        };
    }

    function saveScore() {
        if (!curScore) return { error: "No score open" };
        cmd("file-save");
        return { success: true, message: "Score saved" };
    }

    function openedScoreResult(opened, path) {
        if (!opened || !curScore) return { error: "MuseScore could not open " + path };
        selectionState = { startStaff: 0, endStaff: 1, startTick: 0, elements: {} };
        return {
            success: true,
            path: path,
            scoreName: curScore.scoreName || "",
            numMeasures: curScore.nmeasures,
            numStaves: curScore.nstaves
        };
    }

    function openScore(params) {
        var validation = validateParams(params, ["path"]);
        if (!validation.valid) return validation;
        return openedScoreResult(readScore(params.path), params.path);
    }

    // Close the current score and open the file again, to pick up edits made to it on disk.
    // Save first: closing a modified score would show a dialog.
    function reopenScore(params) {
        if (!curScore) return { error: "No score open" };
        var validation = validateParams(params, ["path"]);
        if (!validation.valid) return validation;
        closeScore(curScore);
        return openedScoreResult(readScore(params.path), params.path);
    }

    // Remove every part that has none of the given staves (MuseScore 4.7+).
    function removeParts(params) {
        if (!curScore) return { error: "No score open" };
        var validation = validateParams(params, ["keepStaves"]);
        if (!validation.valid) return validation;
        if (typeof curScore.removeParts !== "function") {
            return { error: "This MuseScore version cannot remove instruments from a plugin (needs 4.7+). Remove them in the Instruments panel." };
        }

        var parts = curScore.parts;
        var remove = [];
        var kept = [];
        for (var p = 0; p < parts.length; p++) {
            var part = parts[p];
            var keep = false;
            for (var s = Math.floor(part.startTrack / 4); s < Math.ceil(part.endTrack / 4); s++) {
                if (params.keepStaves.indexOf(s) !== -1) keep = true;
            }
            if (keep) kept.push(part.longName || part.partName || ""); else remove.push(part);
        }
        if (kept.length === 0) return { error: "None of the staves " + params.keepStaves.join(", ") + " exist" };

        return executeWithUndo(function() {
            curScore.removeParts(remove);
            return { success: true, removed: remove.length, kept: kept, numStaves: curScore.nstaves };
        });
    }

    // ========================================
    // SCORE ANALYSIS
    // ========================================

    function getScore(params) {
        if (!curScore) return { error: "No score open" };

        try {
            return { success: true, analysis: getScoreSummary(params) };
        } catch (e) {
            return { error: e.toString() };
        }
    }

    // opts: startMeasure / endMeasure (1-based, inclusive), staves (list of staff indices),
    // includeElements (false returns only measure positions, which navigation uses).
    function getScoreSummary(opts) {
        if (!curScore) return { error: "No score open" };

        opts = opts || {};
        var total = curScore.nmeasures;
        var first = Math.max(1, opts.startMeasure || 1);
        var last = Math.min(total, opts.endMeasure || total);
        var includeElements = opts.includeElements !== false;

        var staffIndices = [];
        if (opts.staves && opts.staves.length) {
            for (var s = 0; s < opts.staves.length; s++) {
                if (opts.staves[s] >= 0 && opts.staves[s] < curScore.nstaves) staffIndices.push(opts.staves[s]);
            }
        } else {
            for (var a = 0; a < curScore.nstaves; a++) staffIndices.push(a);
        }

        var score = {
            title: curScore.metaTag("workTitle") || curScore.title || "",
            numMeasures: total,
            startMeasure: first,
            endMeasure: last,
            measures: [],
            staves: []
        };

        var partOf = staffParts();
        for (var i = 0; i < staffIndices.length; i++) {
            var idx = staffIndices[i];
            var staff = curScore.staves && curScore.staves[idx] ||
                       (typeof curScore.staff === "function" ? curScore.staff(idx) : null);
            var part = partOf[idx];
            score.staves.push({
                name: `staff${idx}`,
                index: idx,
                partName: part ? (part.longName || part.partName || "") : "",
                shortName: part ? (part.shortName || "") : (staff ? staff.shortName : ""),
                instrumentId: part ? (part.instrumentId || "") : "",
                isDrums: part ? isDrumPart(part) : false,
                visible: staff ? !staff.invisible : true
            });
        }

        var m = getMeasure(first);
        for (var n = first; m && n <= last; n++, m = m.nextMeasure) {
            var startTick = measureStartTick(m);
            var endTick = measureEndTick(m);
            var entry = { measure: n, startTick: startTick, durationTicks: endTick - startTick };

            if (includeElements) {
                entry.numElements = 0;
                entry.elements = {};
                for (var j = 0; j < staffIndices.length; j++) {
                    entry.elements[`staff${staffIndices[j]}`] = [];
                }

                var seg = m.firstSegment;
                while (seg && seg.tick < endTick) {
                    for (var k = 0; k < staffIndices.length; k++) {
                        for (var v = 0; v < 4; v++) {
                            var processed = processElement(seg.elementAt(staffIndices[k] * 4 + v));
                            if (processed) {
                                processed.startTick = seg.tick;
                                processed.voice = v;
                                entry.elements[`staff${staffIndices[k]}`].push(processed);
                                entry.numElements++;
                            }
                        }
                    }
                    seg = seg.next;
                }
            }
            score.measures.push(entry);
        }
        return score;
    }

    // ========================================
    // INITIALIZATION
    // ========================================

    onRun: {
        console.log("Starting MuseScore API Server (Clean Version) on port 8765");
        
        api.websocketserver.listen(8765, function(clientId) {
            console.log("Client connected with ID: " + clientId);
            clientConnections.push(clientId);
            
            api.websocketserver.onMessage(clientId, function(message) {
                processMessage(message, clientId);
            });
        });
    
        if (curScore) {
            initCursorState();
        }
    }
}